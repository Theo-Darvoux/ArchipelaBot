import contextlib
import io
import re
from typing import TYPE_CHECKING

import discord
from discord import ui

from ..core.yamls import YamlFile
from ..errors import UserError
from ..storage.games import GameRecord
from .components import Tone, notice
from .emojis import E
from .render.signup import game_label, player_label, upload_report
from .render.text import md

if TYPE_CHECKING:
    from .games import GameManager
    from .types import Interaction

CLOSED = "Les inscriptions de cette partie sont fermées."
MAX_FILES = 10


class YamlModal(ui.Modal):
    def __init__(self, games: "GameManager", game: GameRecord) -> None:
        super().__init__(title="Envoyer ton yaml", timeout=900)
        self.games = games
        self.game = game
        self.upload = ui.FileUpload(max_values=MAX_FILES)
        self.add_item(
            ui.Label(
                text="Ton yaml",
                description="Plusieurs fichiers si tu joues plusieurs slots. Un slot déjà envoyé est remplacé.",
                component=self.upload,
            )
        )

    async def on_submit(self, interaction: "Interaction") -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        uploads = await self.games.upload(self.game, interaction.user.id, self.upload.values)
        await interaction.followup.send(view=upload_report(uploads), ephemeral=True)


class MyYamlsView(ui.LayoutView):
    def __init__(self, games: "GameManager", game: GameRecord, user_id: int, files: list[YamlFile]) -> None:
        super().__init__(timeout=600)
        self.games = games
        self.game = game
        self.user_id = user_id
        self._build(files)

    def _build(self, files: list[YamlFile]) -> None:
        self.clear_items()
        mine = [f for f in files if f.user_id == self.user_id]
        container = ui.Container(ui.TextDisplay(f"### {E.yaml} Tes yamls · {md(self.game.name)}"))
        for f in mine:
            slots = "\n".join(f"{player_label(p)} · {game_label(p)}" for p in f.players)
            remove = ui.Button(label="Retirer", style=discord.ButtonStyle.danger)
            remove.callback = self._remove(f)
            container.add_item(ui.Section(ui.TextDisplay(f"`{md(f.filename)}`\n{slots}"), accessory=remove))
        if not mine:
            container.add_item(ui.TextDisplay("Tu n'as envoyé aucun yaml."))
        send = ui.Button(label="Envoyer un yaml", emoji=E.partial("yaml"), style=discord.ButtonStyle.primary)
        send.callback = self._send
        container.add_item(ui.ActionRow(send))
        self.add_item(container)

    def _remove(self, f: YamlFile):
        async def callback(interaction: "Interaction") -> None:
            if self.games.get(self.game.id or 0) is None:
                await interaction.response.edit_message(view=notice(CLOSED, Tone.WARNING))
                return
            assert f.id is not None
            with contextlib.suppress(UserError):
                await self.games.remove(self.game, self.user_id, f.id)
            self._build(await self.games.files(self.game))
            await interaction.response.edit_message(view=self)

        return callback

    async def _send(self, interaction: "Interaction") -> None:
        await interaction.response.send_modal(YamlModal(self.games, self.game))


def _open_game(interaction: "Interaction", game_id: int) -> GameRecord | None:
    return interaction.client.games.get(game_id)


class YamlButton(ui.DynamicItem[ui.Button], template=r"archipelabot:yaml:(?P<game>\d+)"):
    def __init__(self, game_id: int) -> None:
        super().__init__(
            ui.Button(label="Mon yaml", emoji=E.partial("yaml"), style=discord.ButtonStyle.primary,
                      custom_id=f"archipelabot:yaml:{game_id}")
        )  # fmt: skip
        self.game_id = game_id

    @classmethod
    async def from_custom_id(cls, interaction: "Interaction", item: ui.Button, match: re.Match[str]) -> "YamlButton":
        return cls(int(match["game"]))

    async def callback(self, interaction: "Interaction") -> None:
        games = interaction.client.games
        game = _open_game(interaction, self.game_id)
        if game is None:
            await interaction.response.send_message(view=notice(CLOSED, Tone.WARNING), ephemeral=True)
            return
        files = await games.files(game)
        if not any(f.user_id == interaction.user.id for f in files):
            await interaction.response.send_modal(YamlModal(games, game))
            return
        view = MyYamlsView(games, game, interaction.user.id, files)
        await interaction.response.send_message(view=view, ephemeral=True)


class YamlsZipButton(ui.DynamicItem[ui.Button], template=r"archipelabot:yamlzip:(?P<game>\d+)"):
    def __init__(self, game_id: int) -> None:
        super().__init__(
            ui.Button(label="Tous les yamls", emoji=E.partial("zip"), style=discord.ButtonStyle.secondary,
                      custom_id=f"archipelabot:yamlzip:{game_id}")
        )  # fmt: skip
        self.game_id = game_id

    @classmethod
    async def from_custom_id(
        cls, interaction: "Interaction", item: ui.Button, match: re.Match[str]
    ) -> "YamlsZipButton":
        return cls(int(match["game"]))

    async def callback(self, interaction: "Interaction") -> None:
        game = _open_game(interaction, self.game_id)
        if game is None:
            await interaction.response.send_message(view=notice(CLOSED, Tone.WARNING), ephemeral=True)
            return
        content, count = await interaction.client.games.zip(game)
        if not count:
            await interaction.response.send_message(view=notice("Personne n'a encore envoyé de yaml."), ephemeral=True)
            return
        filename = "yamls.zip"
        view = ui.LayoutView()
        view.add_item(
            ui.Container(
                ui.TextDisplay(f"{E.zip} **{count} yamls** · à mettre dans le dossier `Players` pour générer."),
                ui.File(f"attachment://{filename}"),
                accent_colour=Tone.INFO.value,
            )
        )
        await interaction.response.send_message(
            view=view, file=discord.File(io.BytesIO(content), filename), ephemeral=True
        )
