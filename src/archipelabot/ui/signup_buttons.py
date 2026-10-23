import contextlib
import io
import re
from collections.abc import Sequence
from typing import TYPE_CHECKING

import discord
from discord import ui

from ..core.apworlds import Apworld, Attached
from ..core.yamls import YamlFile, unique_filenames
from ..errors import UserError
from ..storage.games import GameRecord
from .components import Tone, notice
from .emojis import E
from .render.signup import (
    INVALID_KEPT,
    Upload,
    apworld_label,
    game_label,
    library_text,
    missing_text,
    player_label,
    upload_text,
    upload_tone,
)
from .render.text import code, md, plural

if TYPE_CHECKING:
    from .games import GameManager
    from .types import Interaction

CLOSED = "Les inscriptions de cette partie sont fermées."
MAX_FILES = 10
MAX_PROMPTS = 5
FILENAME = re.compile(r"[^\w.-]")


def apworld_prompts(
    games: "GameManager",
    game: GameRecord,
    missing: dict[str, list[Apworld]],
    on_pick,
    stray: Sequence[Apworld] = (),
) -> list[ui.Item]:
    """For each game still without its apworld: what to do, and the ones this server already has to pick from."""
    items: list[ui.Item] = []
    for name, library in list(missing.items())[:MAX_PROMPTS]:
        items.append(ui.TextDisplay(missing_text(name, library, stray)))
        if len(library) == 1:
            button = ui.Button(label="Utiliser", emoji=E.partial("apworld"), style=discord.ButtonStyle.primary)
            button.callback = _pick(games, game, library[0].id or 0, on_pick)
            items.append(ui.Section(ui.TextDisplay(library_text(library[0])), accessory=button))
        elif library:
            select = ui.Select(
                placeholder=f"Choisir un apworld de {name}"[:150],
                options=[
                    discord.SelectOption(
                        label=a.label[:100],
                        value=str(a.id),
                        description=f"Envoyé le {a.uploaded_at:%d/%m/%Y}" if a.uploaded_at else None,
                        emoji=E.partial("apworld"),
                    )
                    for a in library[:25]
                ],
            )
            select.callback = _picked_in(games, game, select, on_pick)
            items.append(ui.ActionRow(select))
    return items


def _pick(games: "GameManager", game: GameRecord, apworld_id: int, on_pick):
    async def callback(interaction: "Interaction") -> None:
        try:
            apworld = await games.use_apworld(game, interaction.user.id, apworld_id)
        except UserError as e:
            await interaction.response.send_message(view=notice(str(e), Tone.WARNING), ephemeral=True)
            return
        await on_pick(interaction, apworld)

    return callback


def _picked_in(games: "GameManager", game: GameRecord, select: ui.Select, on_pick):
    async def callback(interaction: "Interaction") -> None:
        await _pick(games, game, int(select.values[0]), on_pick)(interaction)

    return callback


class UploadReport(ui.LayoutView):
    """What was received, then how to get the apworlds still missing for this player's games."""

    def __init__(
        self,
        games: "GameManager",
        game: GameRecord,
        user_id: int,
        uploads: list[Upload],
        missing: dict[str, list[Apworld]],
        stray: Sequence[Apworld] = (),
    ) -> None:
        super().__init__(timeout=900)
        self.games = games
        self.game = game
        self.user_id = user_id
        self.uploads = uploads
        self.stray = stray
        self.picked: list[Apworld] = []
        self.needs_answer = upload_tone(uploads, list(missing)) is not Tone.SUCCESS
        self._build(missing)

    @classmethod
    async def create(
        cls, games: "GameManager", game: GameRecord, user_id: int, uploads: list[Upload]
    ) -> "UploadReport":
        mine = [f for f in await games.files(game) if f.user_id == user_id]
        wanted = {p.game for f in mine for p in f.players}
        stray = [u.apworld for u in uploads if u.apworld and u.apworld.game not in wanted]
        return cls(games, game, user_id, uploads, await games.missing(game, mine), stray)

    def _build(self, missing: dict[str, list[Apworld]]) -> None:
        self.clear_items()
        lines = [upload_text(u) for u in self.uploads]
        lines += [f"{E.apworld} Apworld choisi : {apworld_label(a)}" for a in self.picked]
        prompts = apworld_prompts(self.games, self.game, missing, self._picked, self.stray)
        self.add_item(
            ui.Container(
                ui.TextDisplay("\n".join(lines)),
                *([ui.Separator(), *prompts] if prompts else []),
                accent_colour=upload_tone(self.uploads, list(missing)).value,
            )
        )

    async def _picked(self, interaction: "Interaction", apworld: Apworld) -> None:
        self.picked.append(apworld)
        mine = [f for f in await self.games.files(self.game) if f.user_id == self.user_id]
        self._build(await self.games.missing(self.game, mine))
        await interaction.response.edit_message(view=self)


class YamlModal(ui.Modal):
    def __init__(self, games: "GameManager", game: GameRecord, *, admin: bool = False) -> None:
        super().__init__(title="Envoyer des yamls" if admin else "Envoyer ton yaml", timeout=900)
        self.games = games
        self.game = game
        self.admin = admin
        self.upload = ui.FileUpload(max_values=MAX_FILES)
        self.add_item(
            ui.Label(
                text="Les yamls" if admin else "Ton yaml",
                description="Un yaml remplace celui du même slot, quelle que soit la personne qui l'a envoyé."
                if admin
                else "Un yaml par slot, plus l'apworld si ton jeu n'est pas officiel. Un slot déjà envoyé est "
                "remplacé.",
                component=self.upload,
            )
        )

    async def on_submit(self, interaction: "Interaction") -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        uploads = await self.games.upload(self.game, interaction.user.id, self.upload.values, admin=self.admin)
        report = await UploadReport.create(self.games, self.game, interaction.user.id, uploads)
        await interaction.followup.send(view=report, ephemeral=True)


class MyYamlsView(ui.LayoutView):
    def __init__(
        self,
        games: "GameManager",
        game: GameRecord,
        user_id: int,
        files: list[YamlFile],
        attached: list[Attached],
        missing: dict[str, list[Apworld]],
    ) -> None:
        super().__init__(timeout=600)
        self.games = games
        self.game = game
        self.user_id = user_id
        self._build(files, attached, missing)

    @classmethod
    async def create(cls, games: "GameManager", game: GameRecord, user_id: int) -> "MyYamlsView":
        return cls(games, game, user_id, *await cls._state(games, game, user_id))

    @staticmethod
    async def _state(
        games: "GameManager", game: GameRecord, user_id: int
    ) -> tuple[list[YamlFile], list[Attached], dict[str, list[Apworld]]]:
        files = [f for f in await games.files(game) if f.user_id == user_id]
        attached = await games.attached(game)
        return files, attached, await games.missing(game, files, attached)

    def _build(self, files: list[YamlFile], attached: list[Attached], missing: dict[str, list[Apworld]]) -> None:
        self.clear_items()
        container = ui.Container(ui.TextDisplay(f"### {E.yaml} Tes yamls · {md(self.game.name)}"))
        for f in files:
            if f.invalid:
                slots = f"⚠️ {f.invalid}\n-# {INVALID_KEPT}"
            else:
                slots = "\n".join(f"{player_label(p)} · {game_label(p)}" for p in f.players)
            remove = ui.Button(label="Retirer", style=discord.ButtonStyle.danger)
            remove.callback = self._remove(f)
            container.add_item(ui.Section(ui.TextDisplay(f"{code(f.filename)}\n{slots}"), accessory=remove))
        for a in attached:
            if a.user_id != self.user_id:
                continue
            remove = ui.Button(label="Retirer", style=discord.ButtonStyle.danger)
            remove.callback = self._remove_apworld(a)
            container.add_item(ui.Section(ui.TextDisplay(f"{E.apworld} {apworld_label(a.apworld)}"), accessory=remove))
        if not container.children[1:]:
            container.add_item(ui.TextDisplay("Tu n'as envoyé aucun yaml."))
        if prompts := apworld_prompts(self.games, self.game, missing, self._picked):
            container.add_item(ui.Separator())
            for item in prompts:
                container.add_item(item)
        send = ui.Button(label="Envoyer un fichier", emoji=E.partial("yaml"), style=discord.ButtonStyle.primary)
        send.callback = self._send
        row = ui.ActionRow(send)
        if files:
            download = ui.Button(label="Télécharger mes yamls", style=discord.ButtonStyle.secondary)
            download.callback = self._download
            row.add_item(download)
        container.add_item(row)
        self.add_item(container)

    async def _rebuild(self, interaction: "Interaction") -> None:
        self._build(*await self._state(self.games, self.game, self.user_id))
        await interaction.response.edit_message(view=self)

    async def _picked(self, interaction: "Interaction", _apworld: Apworld) -> None:
        await self._rebuild(interaction)

    def _remove(self, f: YamlFile):
        async def callback(interaction: "Interaction") -> None:
            if self.games.get(self.game.id or 0) is None:
                await interaction.response.edit_message(view=notice(CLOSED, Tone.WARNING))
                return
            assert f.id is not None
            with contextlib.suppress(UserError):
                await self.games.remove(self.game, self.user_id, f.id)
            await self._rebuild(interaction)

        return callback

    def _remove_apworld(self, attached: Attached):
        async def callback(interaction: "Interaction") -> None:
            if self.games.get(self.game.id or 0) is None:
                await interaction.response.edit_message(view=notice(CLOSED, Tone.WARNING))
                return
            with contextlib.suppress(UserError):
                await self.games.remove_apworld(self.game, self.user_id, attached.apworld.id or 0)
            await self._rebuild(interaction)

        return callback

    async def _send(self, interaction: "Interaction") -> None:
        await interaction.response.send_modal(YamlModal(self.games, self.game))

    async def _download(self, interaction: "Interaction") -> None:
        files = [f for f in await self.games.files(self.game) if f.user_id == self.user_id]
        if not files:
            await interaction.response.send_message(view=notice("Tu n'as envoyé aucun yaml."), ephemeral=True)
            return
        named = list(zip(unique_filenames(FILENAME.sub("_", f.filename) for f in files), files, strict=True))
        for start in range(0, len(named), MAX_FILES):
            chunk = named[start : start + MAX_FILES]
            view = ui.LayoutView()
            view.add_item(
                ui.Container(
                    ui.TextDisplay(f"### {E.yaml} Tes yamls · {md(self.game.name)}"),
                    *(ui.File(f"attachment://{name}") for name, _ in chunk),
                    accent_colour=Tone.INFO.value,
                )
            )
            attachments = [discord.File(io.BytesIO(f.content), name) for name, f in chunk]
            if start == 0:
                await interaction.response.send_message(view=view, files=attachments, ephemeral=True)
            else:
                await interaction.followup.send(view=view, files=attachments, ephemeral=True)


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
        user = interaction.user.id
        files = await games.files(game)
        if not any(f.user_id == user for f in files) and not any(a.user_id == user for a in await games.attached(game)):
            await interaction.response.send_modal(YamlModal(games, game))
            return
        view = await MyYamlsView.create(games, game, user)
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
        games = interaction.client.games
        limit = interaction.guild.filesize_limit if interaction.guild else discord.utils.DEFAULT_FILE_SIZE_LIMIT_BYTES
        bundle = await games.bundle(game, limit)
        if not bundle.yamls:
            await interaction.response.send_message(view=notice("Personne n'a encore envoyé de yaml."), ephemeral=True)
            return
        missing = await games.missing(game, await games.files(game))
        count = f"**{plural(bundle.yamls, 'yaml')}**" + (
            f" · **{plural(bundle.apworlds, 'apworld')}**" if bundle.apworlds else ""
        )
        text = (
            f"{E.zip} {count}\nDécompresse dans ton dossier Archipelago : les yamls vont dans `Players`"
            + (", les apworlds dans `custom_worlds`" if bundle.apworlds else "")
            + ". Pense à vider `Players` des yamls d'anciennes parties."
        )
        if invalid := [f for f in await games.files(game) if f.invalid]:
            text += "\n⚠️ Yaml invalide : " + ", ".join(f"{code(f.filename)} (<@{f.user_id}>)" for f in invalid)
        if missing:
            text += "\n⚠️ Apworld manquant : " + ", ".join(f"*{md(name)}*" for name in missing)
        tone = Tone.WARNING if missing or invalid else Tone.INFO
        total = len(bundle.parts)
        for number, content in enumerate(bundle.parts, start=1):
            filename = "archipelago.zip" if total == 1 else f"archipelago-{number}.zip"
            view = ui.LayoutView()
            view.add_item(
                ui.Container(
                    ui.TextDisplay(text if number == 1 else f"{E.zip} Suite ({number}/{total})"),
                    ui.File(f"attachment://{filename}"),
                    accent_colour=tone.value,
                )
            )
            file = discord.File(io.BytesIO(content), filename)
            if number == 1:
                await interaction.response.send_message(view=view, file=file, ephemeral=True)
            else:
                await interaction.followup.send(view=view, file=file, ephemeral=True)
