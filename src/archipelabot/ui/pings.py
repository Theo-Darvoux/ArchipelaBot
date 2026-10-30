from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING

import discord
from discord import ui

from ..storage.claims import NotifMode
from ..storage.pings import ItemPing
from .components import Tone
from .emojis import E
from .render.text import md

if TYPE_CHECKING:
    from .rooms import RoomRuntime
    from .types import Interaction

MAX_SHOWN = 10  # three components each, out of 40

MODE_NOTES = {
    NotifMode.THREAD: "-# Le bot te mentionnera dans le post de la room. `/notifs` pour recevoir ça en DM.",
    NotifMode.DM: "-# Le bot t'enverra un message privé. `/notifs` pour changer.",
    NotifMode.OFF: "⚠️ Tes pings sont coupés : `/notifs` pour les réactiver.",
}


class PingsView(ui.LayoutView):
    """The items a user waits for in a room, each with a button to stop waiting."""

    def __init__(self, runtime: "RoomRuntime", user_id: int, note: str = "") -> None:
        super().__init__(timeout=600)
        self.runtime = runtime
        self.user_id = user_id
        self.note = note
        self._build()

    def _build(self) -> None:
        self.clear_items()
        state = self.runtime.tracker.state
        mine = sorted((p for p in self.runtime.watched if p.user_id == self.user_id), key=lambda p: (p.slot, p.item))
        text = f"### {E.ping} Tes pings · {md(self.runtime.record.name)}"
        if self.note:
            text += f"\n{self.note}"
        container = ui.Container(ui.TextDisplay(text), accent_colour=Tone.INFO.value)
        if not mine:
            container.add_item(
                ui.TextDisplay("Aucun item attendu.\n-# `/ping item:` pour être prévenu quand ton slot le reçoit.")
            )
        for ping in mine[:MAX_SHOWN]:
            button = ui.Button(label="Retirer", style=discord.ButtonStyle.secondary)
            button.callback = self._remove(ping)
            line = f"**{md(ping.item)}** · pour {md(state.name(ping.slot))}"
            container.add_item(ui.Section(ui.TextDisplay(line), accessory=button))
        if len(mine) > MAX_SHOWN:
            container.add_item(ui.TextDisplay(f"-# … et {len(mine) - MAX_SHOWN} autres"))
        if mine:
            container.add_item(ui.TextDisplay(MODE_NOTES[self.runtime.prefs.mode(self.user_id)]))
        self.add_item(container)

    def _remove(self, ping: ItemPing) -> Callable[["Interaction"], Awaitable[None]]:
        async def callback(interaction: "Interaction") -> None:
            await self.runtime.unwatch(ping)
            self.note = f"-# **{md(ping.item)}** retiré."
            self._build()
            await interaction.response.edit_message(view=self)

        return callback
