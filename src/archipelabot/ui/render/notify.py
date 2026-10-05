"""Direct messages for players who prefer them to pings in the room's post."""

import discord
from discord import ui

from ...core import events as ev
from ...core.room import RoomState
from ..emojis import E
from .text import item_emoji, md

MAX_DM_ITEMS = 20


def received_items_view(
    room_name: str, items: list[ev.ItemSent], state: RoomState, jump_url: str | None
) -> ui.LayoutView:
    lines = [
        f"{item_emoji(i.flags)} **{md(i.item)}** de {md(state.name(i.finder))} · *{md(i.location)}*"
        for i in items[:MAX_DM_ITEMS]
    ]
    if len(items) > MAX_DM_ITEMS:
        lines.append(f"… et {len(items) - MAX_DM_ITEMS} autres")
    footer = f"\n-# [Voir la room]({jump_url})" if jump_url else ""
    view = ui.LayoutView()
    view.add_item(
        ui.Container(
            ui.TextDisplay(f"### {E.ping} Nouveaux items · {md(room_name)}\n" + "\n".join(lines) + footer),
            accent_colour=discord.Colour.purple(),
        )
    )
    return view
