"""The status panel: first message of a room's forum post, edited as the game goes."""

from datetime import datetime

import discord
from discord import ui

from ...core.events import ConnectionState
from ...core.progress import Progress
from ...core.room import RoomState
from ..emojis import E
from .text import fit_lines, md

# (icon, label)
CONNECTION_LABELS: dict[ConnectionState, tuple[str, str]] = {
    ConnectionState.CONNECTING: ("connecting", "Connexion…"),
    ConnectionState.CONNECTED: ("online", "Connecté"),
    ConnectionState.RECONNECTING: ("reconnecting", "Reconnexion…"),
    ConnectionState.ASLEEP: ("asleep", "Room endormie"),
    ConnectionState.UNREACHABLE: ("offline", "Serveur injoignable"),
    ConnectionState.FAILED: ("failed", "Connexion refusée"),
    ConnectionState.STOPPED: ("stopped", "Suivi arrêté"),
}

CONNECTION_COLOURS: dict[ConnectionState, discord.Colour] = {
    ConnectionState.CONNECTED: discord.Colour.green(),
    ConnectionState.RECONNECTING: discord.Colour.orange(),
    ConnectionState.ASLEEP: discord.Colour.dark_grey(),
    ConnectionState.UNREACHABLE: discord.Colour.orange(),
    ConnectionState.FAILED: discord.Colour.red(),
    ConnectionState.STOPPED: discord.Colour.dark_grey(),
}

# A message's text displays share 4000 characters; keep room for the header.
ROSTER_BUDGET = 3300


def percent(ratio: float | None) -> str:
    return "? %" if ratio is None else f"{int(ratio * 100)} %"


def progress_icon(slot: int, progress: Progress) -> str:
    return E.goal if progress.reached_goal(slot) else E.ring(progress[slot].ratio)


def presence(slot: int, state: RoomState, progress: Progress) -> str | None:
    """Online dot, or offline dot with the last check time. None when it isn't known."""
    if progress.reached_goal(slot) or state.connection != ConnectionState.CONNECTED:
        return None
    if state.is_online(slot):
        return E.online
    last = progress[slot].last_check
    return f"{E.offline} {discord.utils.format_dt(last, 'R')}" if last else E.offline


def player_line(slot: int, state: RoomState, progress: Progress, claims: dict[int, int], *, detailed: bool) -> str:
    p = progress[slot]
    parts = [f"{progress_icon(slot, progress)} **{percent(p.ratio)}**", md(state.name(slot))]
    if detailed:
        parts.append(f"*{md(state.game(slot))}*")
        if p.deaths:
            parts.append(f"{E.death} {p.deaths}")
        if here := presence(slot, state, progress):
            parts.append(here)
        if slot in claims:
            parts.append(f"<@{claims[slot]}>")
    return " · ".join(parts)


def roster(state: RoomState, progress: Progress, claims: dict[int, int]) -> str:
    """Every player, best first; drops details then players if it doesn't fit in one message."""
    slots = progress.ranking()
    lines = [player_line(s, state, progress, claims, detailed=True) for s in slots]
    if sum(len(line) + 1 for line in lines) > ROSTER_BUDGET:
        lines = [player_line(s, state, progress, claims, detailed=False) for s in slots]
    return fit_lines(lines, ROSTER_BUDGET, lambda hidden: f"… et {hidden} autres · `/status joueur:` pour le détail")


def connection_label(state: ConnectionState) -> str:
    icon, label = CONNECTION_LABELS[state]
    return f"{E.get(icon)} {label}"


def summary_line(state: RoomState, progress: Progress) -> str:
    players = state.players
    goals = sum(progress.reached_goal(s.slot) for s in players)
    total = progress.total
    checks = (
        f"**{progress.done} / {total}** checks · **{int(progress.done / total * 100)} %**"
        if total
        else (f"**{progress.done}** checks")
    )
    return f"{checks} · {goals}/{len(players)} goals"


def panel_view(
    name: str,
    state: RoomState,
    progress: Progress,
    *,
    since: datetime,
    claims: dict[int, int] | None = None,
    page_url: str | None = None,
    buttons: list[ui.Item] | None = None,
) -> ui.LayoutView:
    title = f"## [{md(name)}]({page_url})" if page_url else f"## {md(name)}"
    status = (
        f"{summary_line(state, progress)}\n"
        f"-# {connection_label(state.connection)} · {state.address} · "
        f"suivi depuis {discord.utils.format_dt(since, 'R')}"
    )
    players = roster(state, progress, claims or {}) or "*Aucun joueur*"

    view = ui.LayoutView(timeout=None)
    view.add_item(
        ui.Container(
            ui.TextDisplay(f"{title}\n{status}"),
            ui.Separator(),
            ui.TextDisplay(players),
            *([ui.Separator(), ui.ActionRow(*buttons)] if buttons else []),
            accent_colour=CONNECTION_COLOURS.get(state.connection, discord.Colour.blurple()),
        )
    )
    return view
