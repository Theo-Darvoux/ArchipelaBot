import asyncio
import contextlib
import io
import logging
import zipfile
from collections.abc import AsyncGenerator, Sequence
from pathlib import PurePath
from typing import TYPE_CHECKING

import discord
from discord import ui

from ..core.yamls import (
    MAX_SIZE,
    YamlError,
    YamlFile,
    assign_slots,
    is_yaml_filename,
    parse_players,
    replaced_by,
    warnings,
)
from ..errors import UserError
from ..storage.games import GameRecord, GameRepo
from .forum import RoomTag, ensure_room_tags
from .render.signup import Upload, signup_view
from .rooms import RoomRuntime, ThreadSink
from .signup_buttons import YamlButton, YamlsZipButton

if TYPE_CHECKING:
    from ..bot import ArchipelaBot

log = logging.getLogger(__name__)


class GameManager:
    def __init__(self, bot: "ArchipelaBot") -> None:
        self.bot = bot
        self.repo = GameRepo(bot.db)
        self._games: dict[int, GameRecord] = {}
        self._starting: set[int] = set()
        self._lock = asyncio.Lock()

    async def restore(self) -> None:
        self._games = {game.id: game for game in await self.repo.open() if game.id is not None}
        for game in list(self._games.values()):
            await self.refresh(game)
        log.info("Resumed %d games in sign-up", len(self._games))

    def get(self, game_id: int) -> GameRecord | None:
        return self._games.get(game_id)

    def by_thread(self, thread_id: int | None) -> GameRecord | None:
        return next((g for g in self._games.values() if g.thread_id == thread_id), None)

    async def files(self, game: GameRecord) -> list[YamlFile]:
        assert game.id is not None
        return await self.repo.yamls(game.id)

    async def render(self, game: GameRecord, files: Sequence[YamlFile] | None = None) -> ui.LayoutView:
        assert game.id is not None
        files = await self.files(game) if files is None else files
        config = await self.bot.guild_configs.get(game.guild_id)
        buttons: list[ui.Item] = [YamlButton(game.id), YamlsZipButton(game.id)]
        return signup_view(game, files, ping_role_id=config.ping_role_id, buttons=buttons)

    async def refresh(self, game: GameRecord) -> None:
        try:
            await ThreadSink(self.bot, game).edit_panel(await self.render(game))
        except discord.NotFound:
            log.info("The post of game %s was deleted, cancelling it", game.id)
            self._games.pop(game.id or 0, None)
            await self._cancel(game)
        except discord.HTTPException:
            log.warning("Could not update the panel of game %s", game.id, exc_info=True)

    async def create(self, forum: discord.ForumChannel, name: str, description: str, user_id: int) -> GameRecord:
        game = GameRecord(guild_id=forum.guild.id, name=name, description=description, created_by=user_id)
        await self.repo.create(game)
        assert game.id is not None
        config = await self.bot.guild_configs.get(game.guild_id)
        try:
            tags = await ensure_room_tags(forum)
            created = await forum.create_thread(
                name=name,
                view=await self.render(game, []),
                applied_tags=[tags[RoomTag.SIGNUP]],
                auto_archive_duration=10080,
                allowed_mentions=discord.AllowedMentions(
                    roles=[discord.Object(config.ping_role_id)] if config.ping_role_id else False
                ),
                reason="Nouvelle partie Archipelago",
            )
        except discord.HTTPException as e:
            await self.repo.set_status(game, "cancelled")
            raise UserError(f"Impossible de créer le post dans {forum.mention} ({e.text or e.status}).") from e
        game.thread_id, game.panel_message_id = created.thread.id, created.message.id
        await self.repo.save_post(game)
        self._games[game.id] = game
        try:
            await ThreadSink(self.bot, game).pin_panel()
        except discord.HTTPException:
            log.warning("Could not pin the panel of game %s", game.id, exc_info=True)
        return game

    async def upload(self, game: GameRecord, user_id: int, attachments: Sequence[discord.Attachment]) -> list[Upload]:
        uploads = []
        for attachment in attachments:
            upload = Upload(attachment.filename)
            try:
                if not is_yaml_filename(attachment.filename):
                    raise YamlError("ce n'est pas un fichier `.yaml`.")
                if attachment.size > MAX_SIZE:
                    raise YamlError(f"fichier trop gros (plus de {MAX_SIZE // 1024} Ko).")
                content = await attachment.read()
                new = YamlFile(user_id, PurePath(attachment.filename).name, content, parse_players(content))
                async with self._lock:
                    if self.get(game.id or 0) is None:
                        raise YamlError("les inscriptions de cette partie sont fermées.")
                    replaced = replaced_by(await self.files(game), new)
                    await self.repo.replace_yamls(game.id or 0, new, replaced)
            except YamlError as e:
                upload.error = str(e)
            except discord.HTTPException:
                upload.error = "impossible de télécharger le fichier."
            else:
                upload.players = new.players
                upload.warnings = warnings(new.players)
                upload.replaced = [old.filename for old in replaced]
            uploads.append(upload)
        if any(u.error is None for u in uploads):
            await self.refresh(game)
        return uploads

    async def remove(self, game: GameRecord, user_id: int, yaml_id: int) -> None:
        async with self._lock:
            files = await self.files(game)
            if not any(f.id == yaml_id and f.user_id == user_id for f in files):
                raise UserError("Ce yaml n'est plus là.")
            await self.repo.remove_yaml(yaml_id)
        await self.refresh(game)

    async def zip(self, game: GameRecord) -> tuple[bytes, int]:
        files = await self.files(game)
        buffer = io.BytesIO()
        used: set[str] = set()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
            for f in files:
                name, number = f.filename, 1
                while name.casefold() in used:
                    number += 1
                    path = PurePath(f.filename)
                    name = f"{path.stem} ({number}){path.suffix}"
                used.add(name.casefold())
                archive.writestr(name, f.content)
        return buffer.getvalue(), len(files)

    @contextlib.asynccontextmanager
    async def starting(self, game: GameRecord) -> AsyncGenerator[None]:
        assert game.id is not None
        if self.get(game.id) is None:
            raise UserError("Le suivi de cette partie a déjà démarré.")
        if game.id in self._starting:
            raise UserError("Le suivi de cette partie est déjà en train de démarrer.")
        self._starting.add(game.id)
        try:
            yield
        finally:
            self._starting.discard(game.id)

    async def started(self, game: GameRecord, runtime: RoomRuntime) -> list[int]:
        """Hand the slots to who sent their yaml; returns everyone who sent one."""
        assert game.id is not None
        async with self._lock:
            files = await self.files(game)
            self._games.pop(game.id, None)
            await self.repo.set_status(game, "started")
        for slot, user in assign_slots(files, runtime.tracker.state.players).items():
            if slot not in runtime.claims:
                await runtime.claim(slot, user)
        return list(dict.fromkeys(f.user_id for f in files))

    def thread_deleted(self, thread_id: int) -> None:
        if game := self.by_thread(thread_id):
            self._games.pop(game.id or 0, None)
            asyncio.create_task(self._cancel(game), name=f"cancel game {game.id}")  # noqa: RUF006

    async def _cancel(self, game: GameRecord) -> None:
        try:
            await self.repo.set_status(game, "cancelled")
        except Exception:
            log.exception("Could not cancel game %s", game.id)
