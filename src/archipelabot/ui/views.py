"""Interactive Components V2 views."""

import logging
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING

import discord
from discord import ui

from ..storage.rooms import RoomSettings
from .components import Tone, notice
from .emojis import E
from .render.text import md

if TYPE_CHECKING:
    from .types import Interaction

log = logging.getLogger(__name__)

# (setting, icon, label, help)
SETTING_TOGGLES: list[tuple[str, str, str, str]] = [
    ("show_progression", "prog", "Items de progression", "Items débloquant la progression."),
    ("show_useful", "useful", "Items utiles", "Items utiles pour le gameplay."),
    ("show_traps", "trap", "Pièges", ""),
    ("show_filler", "filler", "Items filler", ""),
    ("show_deaths", "death", "Morts DeathLink", ""),
    ("show_hints", "hint", "Nouveaux hints", ""),
    ("show_joins", "online", "Connexions et déconnexions", ""),
    ("chat_bridge", "chat", "Chat Bridge", "Bridge entre le chat discord et la room."),
]


class SettingsView(ui.LayoutView):
    def __init__(self, room_name: str, settings: RoomSettings, save: Callable[[RoomSettings], Awaitable[None]]) -> None:
        super().__init__(timeout=600)
        self.room_name = room_name
        self.settings = settings
        self.save = save
        self._build()

    def _build(self) -> None:
        self.clear_items()
        container = ui.Container(ui.TextDisplay(f"### {E.settings} Réglages · {md(self.room_name)}"))
        for name, icon, label, help_text in SETTING_TOGGLES:
            enabled = getattr(self.settings, name)
            text = f"{E.get(icon)} **{label}**" + (f"\n-# {help_text}" if help_text else "")
            button = ui.Button(
                label="Affiché" if enabled else "Masqué",
                style=discord.ButtonStyle.success if enabled else discord.ButtonStyle.secondary,
            )
            if name == "chat_bridge":
                button.label = "Activé" if enabled else "Désactivé"
            button.callback = self._toggle_callback(name)
            container.add_item(ui.Section(ui.TextDisplay(text), accessory=button))
        self.add_item(container)

    def _toggle_callback(self, name: str) -> Callable[["Interaction"], Awaitable[None]]:
        async def callback(interaction: "Interaction") -> None:
            setattr(self.settings, name, not getattr(self.settings, name))
            await self.save(self.settings)
            self._build()
            await interaction.response.edit_message(view=self)

        return callback


class ConfirmView(ui.LayoutView):
    """A question with one or more actions, plus "Annuler". Each action returns the message shown afterwards."""

    def __init__(
        self, question: str, actions: list[tuple[str, discord.ButtonStyle, Callable[[], Awaitable[str]]]]
    ) -> None:
        super().__init__(timeout=120)
        self._answered = False
        self.buttons: list[ui.Button] = []
        for label, style, action in actions:
            button = ui.Button(label=label, style=style)
            button.callback = self._run(action)
            self.buttons.append(button)
        cancel = ui.Button(label="Annuler", style=discord.ButtonStyle.secondary)
        cancel.callback = self._cancel
        self.buttons.append(cancel)
        self.add_item(ui.Container(ui.TextDisplay(question), ui.ActionRow(*self.buttons)))

    def _answer(self) -> bool:
        """Only the first click counts; the buttons are disabled for the next ones."""
        if self._answered:
            return False
        self._answered = True
        for button in self.buttons:
            button.disabled = True
        self.stop()
        return True

    def _run(self, action: Callable[[], Awaitable[str]]) -> Callable[["Interaction"], Awaitable[None]]:
        async def callback(interaction: "Interaction") -> None:
            if not self._answer():
                await interaction.response.defer()
                return
            await interaction.response.edit_message(view=self)
            try:
                view = notice(await action(), Tone.SUCCESS)
            except Exception:
                log.exception("Confirmed action failed")
                view = notice("Une erreur inattendue est survenue. Elle a été enregistrée dans les logs.", Tone.ERROR)
            await interaction.edit_original_response(view=view)

        return callback

    async def _cancel(self, interaction: "Interaction") -> None:
        if not self._answer():
            await interaction.response.defer()
            return
        await interaction.response.edit_message(view=notice("Annulé."))
