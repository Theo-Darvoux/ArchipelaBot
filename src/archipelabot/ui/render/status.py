"""/status: one player's card."""

from collections.abc import Callable, Sequence

import discord
from discord import ui

from ...core.logic import SlotLogic
from ...core.progress import Progress
from ...core.room import RoomState
from ..emojis import E
from .panel import in_logic, percent, presence
from .text import item_emoji, md, plural

MAX_IN_LOGIC = 40
IN_LOGIC_BUDGET = 1500


def player_status_view(
    slot: int,
    state: RoomState,
    progress: Progress,
    claims: dict[int, int],
    logic: dict[int, SlotLogic] | None = None,
    location_name: Callable[[int, int], str] | None = None,
    inventory: Sequence[tuple[str, str]] | None = None,
) -> ui.LayoutView:
    p = progress[slot]
    subtitle = f"*{md(state.game(slot))}*" + (f" · joué par <@{claims[slot]}>" if slot in claims else "")
    checks = f"{p.done} / {p.total} checks" if p.total else plural(p.done, "check")
    details = [checks]
    if progress.reached_goal(slot):
        details.append("objectif atteint" + (f" {discord.utils.format_dt(p.goal_at, 'R')}" if p.goal_at else ""))
    elif here := presence(slot, state, progress):
        details.append(f"{here} en jeu" if here == E.online else here)
    if doable := in_logic(slot, progress, logic or {}):
        details.append(doable)
    if p.deaths:
        details.append(f"{E.death} {plural(p.deaths, 'mort')}")

    text = ui.TextDisplay(f"## {md(state.name(slot))}\n{subtitle}\n# {percent(p.ratio)}\n-# " + " · ".join(details))
    icon = E.url("goal" if progress.reached_goal(slot) else E.ring_name(p.ratio))
    items: list[ui.Item] = [ui.Section(text, accessory=ui.Thumbnail(icon)) if icon else text]

    result = (logic or {}).get(slot)
    if result and result.locations and location_name and not progress.reached_goal(slot):
        items += [ui.Separator(), ui.TextDisplay(f"**Checks en logique**\n{_in_logic(slot, result, location_name)}")]
    if inventory:
        items += [
            ui.Separator(),
            ui.TextDisplay(f"**Items de progression comptés par la logique**\n{_counted(inventory)}"),
        ]

    if p.received:
        received = "\n".join(
            f"{item_emoji(i.flags)} **{md(i.item)}** de {md(state.name(i.finder))}" for i in reversed(p.received)
        )
        items += [ui.Separator(), ui.TextDisplay(f"**Derniers items de progression reçus**\n{received}")]

    view = ui.LayoutView()
    view.add_item(ui.Container(*items, accent_colour=discord.Colour(0xAF99EF)))
    return view


def _in_logic(slot: int, result: SlotLogic, location_name: Callable[[int, int], str]) -> str:
    names = sorted((location_name(slot, location) for location in result.locations), key=str.casefold)
    shown: list[str] = []
    for name in names[:MAX_IN_LOGIC]:
        if sum(len(line) + 1 for line in shown) + len(name) + 3 > IN_LOGIC_BUDGET:
            break
        shown.append(f"- {md(name)}")
    if rest := len(names) - len(shown):
        shown.append(f"-# … et {plural(rest, 'autre')}")
    return "\n".join(shown)


def _counted(inventory: Sequence[tuple[str, str]]) -> str:
    sources: dict[str, list[str]] = {}
    for item, source in inventory:
        sources.setdefault(item, []).append(source or "au départ")
    lines: list[str] = []
    rest: list[str] = []
    for item in sorted(sources, key=str.casefold):
        found = sources[item]
        count = f" x{len(found)}" if len(found) > 1 else ""
        line = f"- **{md(item)}**{count} : {md(', '.join(found))}"
        if rest or sum(len(line) + 1 for line in lines) + len(line) > IN_LOGIC_BUDGET:
            rest.append(f"{md(item)}{count}")
        else:
            lines.append(line)
    if rest:
        lines.append(f"-# Aussi : {', '.join(rest)}")
    return "\n".join(lines)
