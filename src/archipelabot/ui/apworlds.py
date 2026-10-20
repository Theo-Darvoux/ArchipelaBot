import contextlib
from collections.abc import Sequence
from typing import TYPE_CHECKING

import discord
from discord import ui

from ..core.apworlds import Apworld, Attached
from ..core.yamls import YamlFile
from ..errors import UserError
from ..storage.games import GameRecord
from .components import Tone, notice
from .emojis import E
from .render.signup import INVALID_KEPT, apworld_label, game_label, player_label
from .render.text import code, md
from .signup_buttons import YamlModal

if TYPE_CHECKING:
    from .games import GameManager
    from .types import Interaction

MAX_WORLDS = 12
MAX_OPTIONS = 25


class GameApworldsView(ui.LayoutView):
    """After the start: which of this server's apworlds generated each world of the game."""

    def __init__(
        self,
        games: "GameManager",
        game: GameRecord,
        files: Sequence[YamlFile],
        custom: dict[str, list[Apworld]],
        attached: Sequence[Attached],
    ) -> None:
        super().__init__(timeout=900)
        self.games = games
        self.game = game
        self._build(files, custom, attached)

    @classmethod
    async def create(cls, games: "GameManager", game: GameRecord) -> "GameApworldsView":
        return cls(games, game, *await cls._state(games, game))

    @staticmethod
    async def _state(
        games: "GameManager", game: GameRecord
    ) -> tuple[list[YamlFile], dict[str, list[Apworld]], list[Attached]]:
        files = await games.files(game)
        return files, await games.custom(game, files), await games.attached(game)

    def _build(self, files: Sequence[YamlFile], custom: dict[str, list[Apworld]], attached: Sequence[Attached]) -> None:
        self.clear_items()
        chosen = {a.apworld.game: a.apworld for a in attached}
        players: dict[str, list[int]] = {}
        for f in files:
            for p in f.players:
                if p.game and f.user_id not in (users := players.setdefault(p.game, [])):
                    users.append(f.user_id)
        send = self.games.bot.command_mention("apworld envoyer")
        container = ui.Container(ui.TextDisplay(f"### {E.apworld} Apworlds · {md(self.game.name)}"))
        if not custom:
            container.add_item(ui.TextDisplay("Aucun jeu de cette partie n'a besoin d'un apworld."))
        for name, library in list(custom.items())[:MAX_WORLDS]:
            users = players.get(name, [])
            current = chosen.get(name)
            text = f"{E.apworld} {apworld_label(current)}" if current else f"⚠️ *{md(name)}* · apworld manquant"
            text += "".join(f" · <@{u}>" for u in users)
            if not library:
                text += f"\n-# Aucun apworld de ce jeu sur le serveur : envoie-le avec {send}."
            container.add_item(ui.TextDisplay(text))
            if library:
                select = ui.Select(
                    placeholder=f"Choisir l'apworld de {name}"[:150],
                    options=[
                        discord.SelectOption(
                            label=a.label[:100],
                            value=str(a.id),
                            description=f"Envoyé le {a.uploaded_at:%d/%m/%Y}" if a.uploaded_at else None,
                            emoji=E.partial("apworld"),
                            default=current is not None and a.id == current.id,
                        )
                        for a in library[:25]
                    ],
                )
                select.callback = self._picked(select, users)
                container.add_item(ui.ActionRow(select))
        container.add_item(
            ui.TextDisplay(f"-# Ensuite, renvoie le spoiler avec {self.games.bot.command_mention('track logique')}.")
        )
        self.add_item(container)

    def _picked(self, select: ui.Select, users: list[int]):
        async def callback(interaction: "Interaction") -> None:
            try:
                await self.games.set_apworld(
                    self.game, users[0] if users else interaction.user.id, int(select.values[0])
                )
            except UserError as e:
                await interaction.response.send_message(view=notice(str(e), Tone.WARNING), ephemeral=True)
                return
            self._build(*await self._state(self.games, self.game))
            await interaction.response.edit_message(view=self)

        return callback


class GameYamlsView(ui.LayoutView):
    """After the start: a manager fixes the post's yamls, so that they regenerate the room."""

    def __init__(self, games: "GameManager", game: GameRecord, files: Sequence[YamlFile]) -> None:
        super().__init__(timeout=900)
        self.games = games
        self.game = game
        self._build(files)

    @classmethod
    async def create(cls, games: "GameManager", game: GameRecord) -> "GameYamlsView":
        return cls(games, game, await games.files(game))

    def _build(self, files: Sequence[YamlFile]) -> None:
        self.clear_items()
        container = ui.Container(ui.TextDisplay(f"### {E.yaml} Yamls · {md(self.game.name)}"))
        lines = []
        for f in files:
            if f.invalid:
                slots = f"⚠️ {f.invalid}\n-# {INVALID_KEPT}"
            else:
                slots = ", ".join(f"{player_label(p)} · {game_label(p)}" for p in f.players)
            lines.append(f"{code(f.filename)} · <@{f.user_id}>\n{slots}")
        container.add_item(ui.TextDisplay("\n".join(lines)[:3500] if lines else "Aucun yaml dans ce post."))
        if files:
            select = ui.Select(
                placeholder="Retirer des yamls",
                max_values=min(len(files), MAX_OPTIONS),
                options=[
                    discord.SelectOption(
                        label=f.filename[:100],
                        value=str(f.id),
                        description=", ".join(p.name for p in f.players)[:100] or None,
                        emoji=E.partial("yaml"),
                    )
                    for f in files[:MAX_OPTIONS]
                ],
            )
            select.callback = self._removed(select)
            container.add_item(ui.ActionRow(select))
        send = ui.Button(label="Envoyer des yamls", emoji=E.partial("yaml"), style=discord.ButtonStyle.primary)
        send.callback = self._send
        container.add_item(ui.ActionRow(send))
        container.add_item(
            ui.TextDisplay(
                "-# Un yaml envoyé ici ou dans ce fil remplace celui du même slot. Ensuite, renvoie le spoiler avec "
                f"{self.games.bot.command_mention('track logique')}."
            )
        )
        self.add_item(container)

    def _removed(self, select: ui.Select):
        async def callback(interaction: "Interaction") -> None:
            for value in select.values:
                with contextlib.suppress(UserError):
                    await self.games.remove(self.game, interaction.user.id, int(value), admin=True)
            self._build(await self.games.files(self.game))
            await interaction.response.edit_message(view=self)

        return callback

    async def _send(self, interaction: "Interaction") -> None:
        await interaction.response.send_modal(YamlModal(self.games, self.game, admin=True))
