"""Room events -> what is posted in the room's post and in its full log."""

from dataclasses import dataclass

import discord
from discord import ui

from ...core import events as ev
from ...core.room import RoomState
from ...storage.rooms import RoomSettings
from ..emojis import E
from .text import item_emoji, md, plural, truncate

GOAL_COLOUR = discord.Colour.gold()


@dataclass(frozen=True, slots=True)
class Line:
    """A compact line, grouped with others into a single message."""

    text: str
    mention: int | None = None


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
    """Several feed lines in one message."""
    view = ui.LayoutView()
    for line in lines:
        view.add_item(ui.TextDisplay(line))
    return view


def log_header(name: str, thread_id: int | None) -> ui.LayoutView:
    view = ui.LayoutView()
    room = f"\nPour en parler : <#{thread_id}>" if thread_id else ""
    view.add_item(
        ui.Container(
            ui.TextDisplay(
                f"## {E.info} {md(name)}\nTous les checks de la room, au fil de la partie.{room}\n"
                "-# `/historique` pour retrouver ce qui concerne un joueur."
            ),
            accent_colour=discord.Colour.blurple(),
        )
    )
    return view


def item_line(item: ev.ItemSent, state: RoomState, ping_user: int | None = None) -> str:
    emoji = item_emoji(item.flags)
    location = f"\n-# {md(item.location)}"
    if item.self_found:
        return f"{emoji} {md(state.name(item.finder))} a trouvé **{md(item.item)}**{location}"
    receiver = f"<@{ping_user}>" if ping_user else md(state.name(item.receiver))
    return f"{emoji} {md(state.name(item.finder))} → **{md(item.item)}** → {receiver}{location}"


def ping_line(item: ev.ItemSent, state: RoomState, user: int, *, own: bool) -> Line:
    """`own`: the user plays the receiving slot, and is named in its place."""
    if own:
        return Line(item_line(item, state, user), user)
    return Line(f"{E.ping} <@{user}> · " + item_line(item, state), user)


def render_event(event: ev.Event, state: RoomState, settings: RoomSettings) -> list[FeedOutput]:
    """The room's full log."""
    match event:
        case ev.ItemSent() if settings.shows_item(event.flags):
            return [Line(item_line(event, state))]
        case ev.Released(slot=slot, items=items):
            receivers = {i.receiver for i in items}
            detail = "plus rien à envoyer"
            if items:
                detail = f"{plural(len(items), 'item')} envoyés à {plural(len(receivers), 'joueur')}"
            return [Line(f"{E.release} **{md(state.name(slot))}** a release · {detail}")]
        case ev.Collected(slot=slot, items=items):
            detail = f"{plural(len(items), 'item récupéré', 'items récupérés')}" if items else "rien à récupérer"
            return [Line(f"{E.collect} **{md(state.name(slot))}** a collect · {detail}")]
        case ev.GoalReached():
            return [Line(goal_text(event, state))]
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


def chat_event(event: ev.Event, state: RoomState, settings: RoomSettings) -> list[FeedOutput]:
    """What still goes in the room's post, where people talk."""
    match event:
        case ev.GoalReached():
            return [Line(goal_text(event, state))]
        case ev.ChatMessage(slot=slot, text=text) if settings.chat_bridge:
            return [Line(f"{E.chat} **{md(state.name(slot))}** : {md(truncate(text, 1500))}")]
    return []


def goal_text(event: ev.GoalReached, state: RoomState) -> str:
    return f"{E.trophy} {md(state.name(event.slot))} a terminé *{md(state.game(event.slot))}* !"


def connection_line(event: ev.ConnectionChanged, address_changed: bool) -> Line | None:
    match event.state:
        case ev.ConnectionState.CONNECTED if address_changed:
            return Line(f"-# {E.online} Reconnecté à la room, nouveau lien · `{event.address}`")
        case ev.ConnectionState.FAILED:
            return Line(
                f"{E.failed} Le serveur a refusé la connexion ({event.detail}). Suivi en pause : "
                f"{E.settings} **Gérer** puis **Reconnecter** sur le panneau pour réessayer."
            )
    return None
