"""What the "Gérer" buttons open: managing a room from its panel, a game from its sign-up post."""

import logging
import re
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

import discord
from discord import ui

from ..core.schedule import ScheduleError, label, parse_start
from ..errors import UserError
from ..storage.games import GameRecord
from ..storage.rooms import RoomRecord
from .apworlds import GameApworldsView, GameYamlsView
from .components import Modal, Tone, View, notice
from .emojis import E
from .forum import TAG_SPECS, RoomTag
from .render.text import md
from .views import ConfirmView, SettingsView

if TYPE_CHECKING:
    from ..bot import ArchipelaBot
    from .rooms import RoomRuntime
    from .types import Interaction

log = logging.getLogger(__name__)

type Callback = Callable[["Interaction"], Awaitable[None]]


def _button(label: str, icon: str, callback: Callback, style=discord.ButtonStyle.secondary) -> ui.Button:
    button = ui.Button(label=label, emoji=E.partial(icon), style=style)
    button.callback = callback
    return button


# --- rooms -----------------------------------------------------------------------------------------


def can_manage(record: RoomRecord, user_id: int, permissions: discord.Permissions) -> bool:
    return user_id == record.created_by or permissions.manage_threads


def room_actions(runtime: "RoomRuntime", game: GameRecord | None) -> Callable[[], list[ui.Button]]:
    async def reconnect(interaction: "Interaction") -> None:
        await interaction.response.send_modal(ReconnectModal(runtime))

    async def apworlds(interaction: "Interaction") -> None:
        assert game is not None
        view = await GameApworldsView.create(interaction.client.games, game)
        await interaction.response.send_message(view=view, ephemeral=True)

    async def yamls(interaction: "Interaction") -> None:
        assert game is not None
        view = await GameYamlsView.create(interaction.client.games, game)
        await interaction.response.send_message(view=view, ephemeral=True)

    async def recap(interaction: "Interaction") -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        await interaction.client.rooms.publish_recap(runtime, announce=False)
        await interaction.followup.send(view=notice("Récap publié.", Tone.SUCCESS), ephemeral=True)

    async def stop(interaction: "Interaction") -> None:
        await interaction.response.send_message(view=stop_view(interaction.client, runtime, game), ephemeral=True)

    def build() -> list[ui.Button]:
        buttons = [_button("Reconnecter", "reconnecting", reconnect)]
        if game is not None:
            buttons += [_button("Apworlds", "apworld", apworlds), _button("Yamls", "yaml", yamls)]
        return [
            *buttons,
            _button("Récap", "trophy", recap),
            _button("Arrêter", "stopped", stop, discord.ButtonStyle.danger),
        ]

    return build


async def room_manage_view(bot: "ArchipelaBot", runtime: "RoomRuntime") -> SettingsView:
    game = await bot.games.repo.started_in(runtime.record.thread_id or 0)

    async def save(settings) -> None:
        runtime.record.settings = settings
        await runtime.repo.save_settings(runtime.record)

    return SettingsView(runtime.record.name, runtime.record.settings.model_copy(), save, room_actions(runtime, game))


def stopped_room_view(record: RoomRecord) -> View:
    """A room no longer tracked: its recap can still be published."""

    async def recap(interaction: "Interaction") -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        await interaction.client.rooms.publish_stopped_recap(record)
        await interaction.followup.send(view=notice("Récap publié.", Tone.SUCCESS), ephemeral=True)

    view = View(timeout=600)
    view.add_item(
        ui.Container(
            ui.TextDisplay(f"### {E.settings} Gérer · {md(record.name)}\n-# Le suivi est arrêté."),
            ui.ActionRow(_button("Publier le récap", "trophy", recap, discord.ButtonStyle.primary)),
            accent_colour=Tone.INFO.value,
        )
    )
    return view


class ReconnectModal(Modal):
    def __init__(self, runtime: "RoomRuntime") -> None:
        super().__init__(title="Reconnecter la room", timeout=600)
        self.runtime = runtime
        self.password = ui.TextInput(
            label="Nouveau mot de passe", placeholder="Vide : garder l'actuel", required=False, max_length=64
        )
        self.add_item(self.password)

    async def on_submit(self, interaction: "Interaction") -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        await interaction.client.rooms.reconnect(self.runtime, self.password.value.strip() or None)
        text = f"{E.reconnecting} Reconnexion lancée : le panneau de la room affiche le résultat."
        await interaction.followup.send(view=notice(text, Tone.SUCCESS), ephemeral=True)


def stop_view(bot: "ArchipelaBot", runtime: "RoomRuntime", game: GameRecord | None) -> ConfirmView:
    def stopped_already() -> bool:
        return bot.rooms.get(runtime.record.id or 0) is not runtime

    async def stop_with_recap() -> str:
        if stopped_already():
            return f"{E.stopped} Ce suivi est déjà arrêté."
        try:
            await bot.rooms.publish_recap(runtime, announce=True)
            text = f"{E.stopped} Suivi arrêté, récap publié."
        except Exception:
            log.exception("Could not publish the recap of room %s", runtime.record.id)
            text = f"{E.stopped} Suivi arrêté, mais le récap n'a pas pu être publié."
        await bot.rooms.stop(runtime, "finished")
        return text

    async def stop() -> str:
        if stopped_already():
            return f"{E.stopped} Ce suivi est déjà arrêté."
        await bot.rooms.stop(runtime, "stopped")
        return f"{E.stopped} Suivi arrêté. Le post reste consultable."

    async def back_to_signup() -> str:
        if game is None or not await bot.rooms.discard(runtime):
            return f"{E.stopped} Ce suivi est déjà arrêté."
        await bot.games.reopen(game)
        try:
            text = f"{E.signup} Suivi annulé : retour aux inscriptions. Relance {start_command} avec le bon lien."
            await runtime.sink.send_view(notice(text))
        except discord.HTTPException:
            log.warning("Could not announce the return to sign-ups of game %s", game.id, exc_info=True)
        return f"{E.signup} Retour aux inscriptions : les yamls sont toujours là."

    start_command = bot.command_mention("track start")
    actions = [
        ("Arrêter et publier le récap", discord.ButtonStyle.primary, stop_with_recap),
        ("Arrêter sans récap", discord.ButtonStyle.danger, stop),
    ]
    question = (
        f"Arrêter le suivi de **{md(runtime.record.name)}** ?\n-# Le post restera consultable, avec le tag "
        f"{' '.join(TAG_SPECS[RoomTag.FINISHED][::-1])}."
    )
    if runtime.record.hosting:
        question += "\n-# Le bot héberge cette partie : son serveur sera arrêté, personne ne pourra plus jouer."
    if game is not None:
        actions.append(("Revenir aux inscriptions", discord.ButtonStyle.secondary, back_to_signup))
        question += (
            "\n-# **Revenir aux inscriptions** : mauvais lien ou partie à regénérer. Le suivi est oublié (pas de "
            f"récap), les yamls restent, et tu pourras relancer {start_command}."
            + (" La seed hébergée est supprimée." if runtime.record.hosting else "")
        )
    return ConfirmView(question, actions)


# --- games in sign-up ------------------------------------------------------------------------------


def can_organise(game: GameRecord, user_id: int, permissions: discord.Permissions) -> bool:
    return user_id == game.created_by or permissions.manage_threads


class GameManageView(View):
    def __init__(self, game: GameRecord) -> None:
        super().__init__(timeout=600)
        self.game = game
        self.add_item(
            ui.Container(
                ui.TextDisplay(f"### {E.settings} Gérer · {md(game.name)}"),
                ui.ActionRow(
                    _button("Modifier", "settings", self._edit, discord.ButtonStyle.primary),
                    _button("Yamls", "yaml", self._yamls),
                    _button("Annuler la partie", "stopped", self._cancel, discord.ButtonStyle.danger),
                ),
                ui.TextDisplay(
                    "-# **Modifier** : nom, date et description · **Yamls** : voir, retirer ou remplacer les yamls "
                    "de n'importe qui"
                ),
                accent_colour=Tone.INFO.value,
            )
        )

    async def _edit(self, interaction: "Interaction") -> None:
        await interaction.response.send_modal(EditGameModal(self.game, ZoneInfo(interaction.client.settings.timezone)))

    async def _yamls(self, interaction: "Interaction") -> None:
        view = await GameYamlsView.create(interaction.client.games, self.game)
        await interaction.response.send_message(view=view, ephemeral=True)

    async def _cancel(self, interaction: "Interaction") -> None:
        games = interaction.client.games
        cancelled_tag = " ".join(TAG_SPECS[RoomTag.CANCELLED][::-1])

        async def cancel() -> str:
            if not await games.cancel(self.game):
                return "Cette partie n'est plus en inscriptions."
            return f"{E.stopped} Partie annulée. Le post reste consultable, avec le tag {cancelled_tag}."

        view = ConfirmView(
            f"Annuler **{md(self.game.name)}** ?\n-# Les inscriptions seront fermées et le post gardera la liste des "
            f"inscrits, avec le tag {cancelled_tag}.",
            [("Annuler la partie", discord.ButtonStyle.danger, cancel)],
            cancel_label="Garder la partie",
        )
        await interaction.response.send_message(view=view, ephemeral=True)


class EditGameModal(Modal):
    def __init__(self, game: GameRecord, tz: ZoneInfo) -> None:
        super().__init__(title="Modifier la partie", timeout=900)
        self.game = game
        self.tz = tz
        self.name = ui.TextInput(label="Nom", default=game.name, max_length=90)
        self.start = ui.TextInput(
            label="Début",
            placeholder="samedi 21h, demain 20h30, 12/10 21h… · vide : pas de date",
            default=label(game.starts_at.astimezone(tz)) if game.starts_at else None,
            required=False,
            max_length=60,
        )
        self.description = ui.TextInput(
            label="Description",
            style=discord.TextStyle.paragraph,
            default=game.description or None,
            required=False,
            max_length=1000,
        )
        for item in (self.name, self.start, self.description):
            self.add_item(item)

    def _starts_at(self) -> datetime | None:
        text = self.start.value.strip()
        if not text:
            return None
        if self.game.starts_at and text == label(self.game.starts_at.astimezone(self.tz)):
            return self.game.starts_at
        try:
            return parse_start(re.sub(r"\bà\b", "", text), datetime.now(self.tz)).astimezone(UTC)
        except ScheduleError as e:
            raise UserError(f"Début de partie : {e}") from e

    async def on_submit(self, interaction: "Interaction") -> None:
        starts_at = self._starts_at()
        await interaction.client.games.edit(
            self.game,
            name=self.name.value.strip() or self.game.name,
            description=self.description.value.strip(),
            starts_at=starts_at,
        )
        await interaction.response.send_message(view=notice("Partie mise à jour.", Tone.SUCCESS), ephemeral=True)


class GameManageButton(ui.DynamicItem[ui.Button], template=r"archipelabot:game:(?P<game>\d+)"):
    def __init__(self, game_id: int) -> None:
        super().__init__(
            ui.Button(label="Gérer", emoji=E.partial("settings"), style=discord.ButtonStyle.secondary,
                      custom_id=f"archipelabot:game:{game_id}")
        )  # fmt: skip
        self.game_id = game_id

    @classmethod
    async def from_custom_id(
        cls, interaction: "Interaction", item: ui.Button, match: re.Match[str]
    ) -> "GameManageButton":
        return cls(int(match["game"]))

    async def callback(self, interaction: "Interaction") -> None:
        game = interaction.client.games.get(self.game_id)
        if game is None:
            view = notice("Cette partie n'est plus en inscriptions.", Tone.WARNING)
        elif not can_organise(game, interaction.user.id, interaction.permissions):
            view = notice("Seule la personne qui a annoncé la partie (ou un modérateur) peut la gérer.", Tone.ERROR)
        else:
            view = GameManageView(game)
        await interaction.response.send_message(view=view, ephemeral=True)
