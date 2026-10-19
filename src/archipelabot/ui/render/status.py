"""/status: one player's card."""

import discord
from discord import ui

from ...core.logic import SlotLogic
from ...core.progress import Progress
from ...core.room import RoomState
from ..emojis import E
from .panel import in_logic, percent, presence
from .text import item_emoji, md, plural


def player_status_view(
    slot: int, state: RoomState, progress: Progress, claims: dict[int, int], logic: dict[int, SlotLogic] | None = None
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

    if p.received:
        received = "\n".join(
            f"{item_emoji(i.flags)} **{md(i.item)}** de {md(state.name(i.finder))}" for i in reversed(p.received)
        )
        items += [ui.Separator(), ui.TextDisplay(f"**Derniers items de progression reçus**\n{received}")]

    view = ui.LayoutView()
    view.add_item(ui.Container(*items, accent_colour=discord.Colour(0xAF99EF)))
    return view
