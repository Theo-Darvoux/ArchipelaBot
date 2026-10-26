"""Buttons on the panel ("Je joue", "Mes patchs", "Mes hints", "Réglages") and what they open."""

import asyncio
import re
from pathlib import Path
from typing import TYPE_CHECKING

import discord
from discord import ui

from ..core.hosting import slot_files
from ..errors import UserError
from ..storage.claims import NotifMode
from .components import Tone, notice
from .emojis import E
from .render.hints import all_hints_view, player_hints_view
from .render.text import md, truncate

if TYPE_CHECKING:
    from .rooms import RoomRuntime
    from .types import Interaction

MAX_OPTIONS = 25  # Discord's limit for a select menu
MAX_FILES = 10  # per message
FILENAME = re.compile(r"[^\w.-]")

NOTIF_NOTES = {
    NotifMode.THREAD: "-# Si tu reçois un item de progression pendant que tu n'es pas en jeu, le bot te mentionnera "
    "dans le post de la room, une fois par absence. `/notifs` pour recevoir ça en DM, ou plus du tout.",
    NotifMode.DM: "-# Si tu reçois un item de progression pendant que tu n'es pas en jeu, le bot t'enverra un message "
    "privé, une fois par absence. `/notifs` pour changer.",
    NotifMode.OFF: "-# Tu as désactivé les notifications d'items. `/notifs` pour les réactiver.",
}


def claimed_message(runtime: "RoomRuntime", slot: int, user_id: int) -> str:
    info = runtime.tracker.state.slots[slot]
    return f"Tu joues **{md(info.display)}** · *{md(info.game)}*.\n{NOTIF_NOTES[runtime.prefs.mode(user_id)]}"


class ClaimPicker(ui.LayoutView):
    def __init__(self, runtime: "RoomRuntime", user_id: int) -> None:
        super().__init__(timeout=300)
        self.runtime = runtime
        mine = [s for s in runtime.tracker.state.players if runtime.claims.get(s.slot) == user_id]
        free = [s for s in runtime.tracker.state.players if s.slot not in runtime.claims]

        text = f"### {E.claim} Quel slot est le tien ?"
        if mine:
            text += "\nTu joues déjà : " + ", ".join(f"**{md(s.display)}**" for s in mine)
        container = ui.Container(ui.TextDisplay(text))
        if free:
            select = ui.Select(
                placeholder="Choisis ton slot",
                options=[
                    discord.SelectOption(
                        label=truncate(s.display, 100), description=truncate(s.game, 100), value=str(s.slot)
                    )
                    for s in free[:MAX_OPTIONS]
                ],
            )
            select.callback = self._picked
            self.select = select
            container.add_item(ui.ActionRow(select))
            if len(free) > MAX_OPTIONS:
                container.add_item(ui.TextDisplay("-# Ton slot n'est pas dans la liste ? Utilise `/claim`."))
        else:
            container.add_item(
                ui.TextDisplay("Tous les slots sont déjà pris. Un modérateur peut réattribuer un slot avec `/claim`.")
            )
        self.add_item(container)

    async def _picked(self, interaction: "Interaction") -> None:
        slot = int(self.select.values[0])
        try:
            await self.runtime.claim(slot, interaction.user.id)
        except UserError as e:
            await interaction.response.edit_message(view=notice(str(e), Tone.ERROR))
            return
        text = claimed_message(self.runtime, slot, interaction.user.id)
        await interaction.response.edit_message(view=notice(text, Tone.SUCCESS))


class ClaimButton(ui.DynamicItem[ui.Button], template=r"archipelabot:claim:(?P<room>\d+)"):
    """Lives on the panel; works across restarts because the room id is in its custom id."""

    def __init__(self, room_id: int) -> None:
        super().__init__(
            ui.Button(label="Je joue", emoji=E.partial("claim"), style=discord.ButtonStyle.primary,
                      custom_id=f"archipelabot:claim:{room_id}")
        )  # fmt: skip
        self.room_id = room_id

    @classmethod
    async def from_custom_id(cls, interaction: "Interaction", item: ui.Button, match: re.Match[str]) -> "ClaimButton":
        return cls(int(match["room"]))

    async def callback(self, interaction: "Interaction") -> None:
        runtime = interaction.client.rooms.get(self.room_id)
        if runtime is None:
            await interaction.response.send_message(
                view=notice("Cette room n'est plus suivie.", Tone.WARNING), ephemeral=True
            )
            return
        await interaction.response.send_message(view=ClaimPicker(runtime, interaction.user.id), ephemeral=True)


class SettingsButton(ui.DynamicItem[ui.Button], template=r"archipelabot:settings:(?P<room>\d+)"):
    def __init__(self, room_id: int) -> None:
        super().__init__(
            ui.Button(label="Réglages", emoji=E.partial("settings"), style=discord.ButtonStyle.secondary,
                      custom_id=f"archipelabot:settings:{room_id}")
        )  # fmt: skip
        self.room_id = room_id

    @classmethod
    async def from_custom_id(
        cls, interaction: "Interaction", item: ui.Button, match: re.Match[str]
    ) -> "SettingsButton":
        return cls(int(match["room"]))

    async def callback(self, interaction: "Interaction") -> None:
        runtime = interaction.client.rooms.get(self.room_id)
        if runtime is None:
            view = notice("Cette room n'est plus suivie.", Tone.WARNING)
        elif not runtime.can_manage(interaction.user.id, interaction.permissions):
            view = notice("Seule la personne qui a lancé le suivi (ou un modérateur) peut changer les réglages.",
                          Tone.ERROR)  # fmt: skip
        else:
            view = runtime.settings_view()
        await interaction.response.send_message(view=view, ephemeral=True)


class MyHintsButton(ui.DynamicItem[ui.Button], template=r"archipelabot:hints:(?P<room>\d+)"):
    def __init__(self, room_id: int) -> None:
        super().__init__(
            ui.Button(label="Mes hints", emoji=E.partial("hint"), style=discord.ButtonStyle.secondary,
                      custom_id=f"archipelabot:hints:{room_id}")
        )  # fmt: skip
        self.room_id = room_id

    @classmethod
    async def from_custom_id(cls, interaction: "Interaction", item: ui.Button, match: re.Match[str]) -> "MyHintsButton":
        return cls(int(match["room"]))

    async def callback(self, interaction: "Interaction") -> None:
        runtime = interaction.client.rooms.get(self.room_id)
        if runtime is None:
            view = notice("Cette room n'est plus suivie.", Tone.WARNING)
        else:
            view = hints_for(runtime, interaction.user.id)
        await interaction.response.send_message(view=view, ephemeral=True)


def hints_for(runtime: "RoomRuntime", user_id: int) -> ui.LayoutView:
    """The user's own hints, or every hint if they haven't said which slot they play."""
    slots = [slot for slot, user in runtime.claims.items() if user == user_id]
    if slots:
        return player_hints_view(slots, runtime.tracker.state, you=True)
    return all_hints_view(runtime.tracker.state)


class PatchButton(ui.DynamicItem[ui.Button], template=r"archipelabot:patch:(?P<room>\d+)"):
    """On the panel of a room the bot hosts, when some games need a file to play."""

    def __init__(self, room_id: int) -> None:
        super().__init__(
            ui.Button(label="Mes patchs", emoji=E.partial("patch"), style=discord.ButtonStyle.secondary,
                      custom_id=f"archipelabot:patch:{room_id}")
        )  # fmt: skip
        self.room_id = room_id

    @classmethod
    async def from_custom_id(cls, interaction: "Interaction", item: ui.Button, match: re.Match[str]) -> "PatchButton":
        return cls(int(match["room"]))

    async def callback(self, interaction: "Interaction") -> None:
        runtime = interaction.client.rooms.get(self.room_id)
        if runtime is None or runtime.record.hosting is None:
            await interaction.response.send_message(
                view=notice("Cette room n'est plus suivie.", Tone.WARNING), ephemeral=True
            )
            return
        files = await asyncio.to_thread(slot_files, interaction.client.hosting.folder(runtime.record.hosting))
        mine = [slot for slot, user in sorted(runtime.claims.items()) if user == interaction.user.id]
        if mine or not files:
            await send_patches(interaction, runtime, mine, files)
        else:
            await interaction.response.send_message(view=PatchPicker(runtime, files), ephemeral=True)


class PatchPicker(ui.LayoutView):
    def __init__(self, runtime: "RoomRuntime", files: dict[int, list[Path]]) -> None:
        super().__init__(timeout=300)
        self.runtime = runtime
        self.files = files
        slots = [s for s in runtime.tracker.state.players if s.slot in files]
        container = ui.Container(
            ui.TextDisplay(f"### {E.patch} Le patch de quel slot ?\n-# Avec **Je joue**, tu as directement le tien.")
        )
        select = ui.Select(
            placeholder="Choisis un slot",
            options=[
                discord.SelectOption(
                    label=truncate(s.display, 100), description=truncate(s.game, 100), value=str(s.slot)
                )
                for s in slots[:MAX_OPTIONS]
            ],
        )
        select.callback = self._picked
        self.select = select
        container.add_item(ui.ActionRow(select))
        self.add_item(container)

    async def _picked(self, interaction: "Interaction") -> None:
        await send_patches(interaction, self.runtime, [int(self.select.values[0])], self.files)


async def send_patches(
    interaction: "Interaction", runtime: "RoomRuntime", slots: list[int], files: dict[int, list[Path]]
) -> None:
    """The files to play these slots, privately, or why there are none."""
    state = runtime.tracker.state
    limit = interaction.guild.filesize_limit if interaction.guild else discord.utils.DEFAULT_FILE_SIZE_LIMIT_BYTES
    wanted = [(slot, path) for slot in slots for path in files.get(slot, [])]
    sizes = await asyncio.to_thread(lambda: {path: path.stat().st_size for _, path in wanted})
    sendable = [(slot, path) for slot, path in wanted if sizes[path] <= limit]
    too_big = [path.name for _, path in wanted if sizes[path] > limit]
    notes = [f"-# `{name}` est trop gros pour Discord : demande-le à l'hôte." for name in too_big]
    without = [md(state.name(slot)) for slot in slots if slot not in files]
    if without:
        address = runtime.play_address
        notes.append(f"-# Pas de patch pour {', '.join(without)} : connecte-toi directement à `{address}`.")
    if not sendable:
        view = notice("\n".join(notes) or "Pas de patch pour ce slot.", Tone.INFO)
        await interaction.response.send_message(view=view, ephemeral=True)
        return
    for start in range(0, len(sendable), MAX_FILES):
        chunk = sendable[start : start + MAX_FILES]
        text = f"### {E.patch} Pour jouer · `{runtime.play_address}`"
        if start == 0 and notes:
            text += "\n" + "\n".join(notes)
        view = ui.LayoutView()
        view.add_item(
            ui.Container(
                ui.TextDisplay(text),
                *(ui.File(f"attachment://{FILENAME.sub('_', path.name)}") for _, path in chunk),
                accent_colour=Tone.INFO.value,
            )
        )
        attachments = [discord.File(path, FILENAME.sub("_", path.name)) for _, path in chunk]
        if start == 0:
            await interaction.response.send_message(view=view, files=attachments, ephemeral=True)
        else:
            await interaction.followup.send(view=view, files=attachments, ephemeral=True)
