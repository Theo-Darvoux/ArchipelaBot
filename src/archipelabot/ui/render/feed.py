"""Room events -> what is posted in the room's forum post."""

from collections import defaultdict
from collections.abc import Callable, Sequence
from dataclasses import dataclass

import discord
from discord import ui

from ...ap.protocol import ItemFlags
from ...core import events as ev
from ...core.room import RoomState
from ...storage.rooms import RoomSettings
from ..emojis import E
from .text import item_emoji, md, plural, truncate

GOAL_COLOUR = discord.Colour.gold()


@dataclass(frozen=True, slots=True)
class Ping:
    """A Discord user to notify about the progression items they received."""

    user: int
    items: tuple[ev.ItemSent, ...]


@dataclass(frozen=True, slots=True)
class Line:
    """A compact line, grouped with others into a single message."""

    text: str
    ping: Ping | None = None


@dataclass(frozen=True, slots=True)
class Card:
    """A highlighted standalone message."""

    title: str
    body: str
    colour: discord.Colour

    def view(self) -> ui.LayoutView:
        view = ui.LayoutView()
        view.add_item(ui.Container(ui.TextDisplay(f"### {self.title}\n{self.body}"), accent_colour=self.colour))
        return view


type FeedOutput = Line | Card


def feed_view(lines: list[str]) -> ui.LayoutView:
    """Several feed lines in one message, with a divider between each."""
    view = ui.LayoutView()
    for i, line in enumerate(lines):
        if i:
            view.add_item(ui.Separator(spacing=discord.SeparatorSpacing.small))
        view.add_item(ui.TextDisplay(line))
    return view


type Pinger = Callable[[int], int | None]

MAX_LISTED_ITEMS = 8


def no_ping(_slot: int) -> None:
    return None


def item_line(item: ev.ItemSent, state: RoomState, ping_user: int | None = None) -> str:
    emoji = item_emoji(item.flags)
    location = f"\n-# {md(item.location)}"
    if item.self_found:
        return f"{emoji} {md(state.name(item.finder))} a trouvé **{md(item.item)}**{location}"
    receiver = f"<@{ping_user}>" if ping_user else md(state.name(item.receiver))
    return f"{emoji} {md(state.name(item.finder))} → **{md(item.item)}** → {receiver}{location}"


def pinged_user(item: ev.ItemSent, ping: Pinger) -> int | None:
    if item.self_found or not item.flags & ItemFlags.PROGRESSION:
        return None
    return ping(item.receiver)


def item_list(items: Sequence[ev.ItemSent]) -> str:
    names = ", ".join(f"**{md(i.item)}**" for i in items[:MAX_LISTED_ITEMS])
    return names + (f" et {len(items) - MAX_LISTED_ITEMS} autres" if len(items) > MAX_LISTED_ITEMS else "")


def render_event(event: ev.Event, state: RoomState, settings: RoomSettings, ping: Pinger = no_ping) -> list[FeedOutput]:
    match event:
        case ev.ItemSent():
            if user := pinged_user(event, ping):
                return [Line(item_line(event, state, user), Ping(user, (event,)))]
            return [Line(item_line(event, state))] if settings.shows_item(event.flags) else []
        case ev.Released(slot=slot, items=items):
            receivers = {i.receiver for i in items}
            detail = "plus rien à envoyer"
            if items:
                detail = f"{plural(len(items), 'item')} envoyés à {plural(len(receivers), 'joueur')}"
            lines: list[FeedOutput] = [Line(f"{E.release} **{md(state.name(slot))}** a release · {detail}")]
            by_user: dict[int, list[ev.ItemSent]] = defaultdict(list)
            for item in items:
                if user := pinged_user(item, ping):
                    by_user[user].append(item)
            for user, received in by_user.items():
                count = plural(len(received), "item de progression", "items de progression")
                text = f"{E.ping} <@{user}> · {count} grâce à ce release : {item_list(received)}"
                lines.append(Line(text, Ping(user, tuple(received))))
            return lines
        case ev.Collected(slot=slot, items=items):
            detail = f"{plural(len(items), 'item récupéré', 'items récupérés')}" if items else "rien à récupérer"
            return [Line(f"{E.collect} **{md(state.name(slot))}** a collect · {detail}")]
        case ev.GoalReached(slot=slot):
            body = f"Objectif atteint sur *{md(state.game(slot))}*."
            return [Card(f"{E.trophy} {md(state.name(slot))} a terminé !", body, GOAL_COLOUR)]
        case ev.ChatMessage(slot=slot, text=text) if settings.chat_bridge:
            return [Line(f"{E.chat} **{md(state.name(slot))}** : {md(truncate(text, 1500))}")]
        case ev.Death(source=source, cause=cause) if settings.show_deaths:
            return [
                Line(f"{E.death} Mort de **{md(source)}**" + (f" · « {md(truncate(cause, 300))} »" if cause else ""))
            ]
        case ev.HintAdded(hint=hint) if settings.show_hints:
            line = f"{E.hint} {item_emoji(hint.flags)} **{md(hint.item)}** pour {md(state.name(hint.receiver))}"
            return [Line(f"{line} · chez {md(state.name(hint.finder))}\n-# {md(hint.location)}")]
        case ev.PlayerJoined(slot=slot) if settings.show_joins:
            return [Line(f"-# {E.online} {md(state.name(slot))} a rejoint la partie")]
        case ev.PlayerLeft(slot=slot) if settings.show_joins:
            return [Line(f"-# {E.offline} {md(state.name(slot))} a quitté la partie")]
    return []


def waiting_line(ping: Ping) -> Line:
    count = plural(len(ping.items), "item de progression t'attend", "items de progression t'attendent")
    return Line(f"{E.ping} <@{ping.user}> · {count} : {item_list(ping.items)}", ping)


def connection_line(event: ev.ConnectionChanged, downtime: float | None) -> Line | None:
    match event.state:
        case ev.ConnectionState.CONNECTED if downtime is not None and downtime >= 30:
            return Line(f"-# {E.online} Reconnecté à la room · `{event.address}`")
        case ev.ConnectionState.ASLEEP:
            return Line(
                f"-# {E.offline} La room s'est endormie. "
                "Le suivi reprendra tout seul dès que quelqu'un ouvrira sa page."
            )
        case ev.ConnectionState.FAILED:
            return Line(f"{E.failed} Le serveur a refusé la connexion ({event.detail}). Suivi en pause.")
    return None
