import asyncio
import contextlib
import io
import logging
import zipfile
from collections.abc import AsyncGenerator, Iterable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import PurePath
from typing import TYPE_CHECKING

import discord
from discord import ui

from ..ap.webhost import WebhostError
from ..core.apworlds import MAX_SIZE as MAX_APWORLD_SIZE
from ..core.apworlds import Apworld, ApworldError, Attached, is_apworld_filename, read_apworld
from ..core.compat import Problem, check_options, version_conflicts
from ..core.logic import DEFAULT_PLANDO, find_installs, pick_install
from ..core.room import SlotInfo
from ..core.yamls import (
    MAX_SIZE,
    PlayerYaml,
    YamlError,
    YamlFile,
    assign_slots,
    generation_order,
    is_yaml_filename,
    read_yaml,
    replaced_by,
    unique_filenames,
    warnings,
)
from ..errors import UserError
from ..storage.apworlds import ApworldRepo
from ..storage.games import GameRecord, GameRepo
from ..storage.guilds import GuildConfig
from .forum import RoomTag, ensure_room_tags
from .render.signup import Upload, conflict_text, signup_view
from .rooms import RoomRuntime, ThreadSink
from .signup_buttons import CheckButton, YamlButton, YamlsZipButton

if TYPE_CHECKING:
    from ..bot import ArchipelaBot

log = logging.getLogger(__name__)

OFFICIAL_WEBHOST = "https://archipelago.gg"
ZIP_OVERHEAD = 64 * 1024


def is_signup_file(filename: str) -> bool:
    return is_yaml_filename(filename) or is_apworld_filename(filename)


def check_can_create(config: GuildConfig, permissions: discord.Permissions) -> None:
    if config.admin_only_games and not permissions.manage_guild:
        raise UserError("Sur ce serveur, seuls les admins peuvent créer des parties.")


class CoreGames:
    """Games that ship with Archipelago (no apworld needed), as archipelago.gg lists them; None until known."""

    REFRESH = timedelta(days=1)
    RETRY = timedelta(minutes=10)

    def __init__(self, bot: "ArchipelaBot") -> None:
        self.bot = bot
        self.games: frozenset[str] | None = None
        self._next = datetime.min.replace(tzinfo=UTC)
        self._lock = asyncio.Lock()

    async def get(self) -> frozenset[str] | None:
        async with self._lock:
            now = datetime.now(UTC)
            if now >= self._next and self.bot.http_session is not None:
                try:
                    self.games = await self.bot.webhost.games(OFFICIAL_WEBHOST)
                    self._next = now + self.REFRESH
                except WebhostError as e:
                    log.warning("Could not list the core games: %s", e)
                    self._next = now + self.RETRY
        return self.games


@dataclass(slots=True)
class Bundle:
    parts: list[bytes]
    yamls: int
    apworlds: int


@dataclass(slots=True)
class ApworldImport:
    added: list[Apworld] = field(default_factory=list)
    known: int = 0
    refused: list[tuple[str, str]] = field(default_factory=list)  # filename, why


class GameManager:
    def __init__(self, bot: "ArchipelaBot") -> None:
        self.bot = bot
        self.repo = GameRepo(bot.db)
        self.apworlds = ApworldRepo(bot.db)
        self.core = CoreGames(bot)
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

    async def attached(self, game: GameRecord) -> list[Attached]:
        assert game.id is not None
        return await self.apworlds.attached(game.id)

    async def missing(
        self, game: GameRecord, files: Sequence[YamlFile], attached: Sequence[Attached] | None = None
    ) -> dict[str, list[Apworld]]:
        """Games of these yamls still without their apworld, with the ones this server already has for them."""
        attached = await self.attached(game) if attached is None else attached
        have = {a.apworld.game for a in attached}
        return {name: library for name, library in (await self.custom(game, files)).items() if name not in have}

    async def custom(self, game: GameRecord, files: Sequence[YamlFile]) -> dict[str, list[Apworld]]:
        """Games of these yamls that need an apworld, with the ones this server has for them."""
        wanted = sorted({p.game for f in files for p in f.players if p.game})
        core = await self.core.get()
        library = await self.apworlds.library(game.guild_id, wanted)
        known = {a.game for a in library}
        return {
            name: [a for a in library if a.game == name]
            for name in wanted
            if (name not in core if core is not None else name in known)
        }

    async def conflicts(
        self, game: GameRecord, players: Iterable[PlayerYaml], attached: Sequence[Attached] | None = None
    ) -> list[str]:
        """Versions of the game's apworlds these players' yamls don't accept, as their manifests and yamls say."""
        attached = await self.attached(game) if attached is None else attached
        return [conflict_text(*c) for c in version_conflicts(players, {a.apworld.game: a.apworld for a in attached})]

    async def check(self, game: GameRecord) -> tuple[str, list[Problem]]:
        """Every option of the yamls against the apworlds, in Archipelago: runs the apworlds' code.

        Returns the version of Archipelago used. Raises LogicError when it can't run."""
        settings = self.bot.settings
        installs = await asyncio.to_thread(find_installs, settings.archipelago_path, settings.archipelago_python)
        install = pick_install(installs, "")
        entries, _, _ = await self.entries(game, used_only=True)
        return install.version, await check_options(install.python, install.path, entries, DEFAULT_PLANDO)

    async def render(self, game: GameRecord, files: Sequence[YamlFile] | None = None) -> ui.LayoutView:
        assert game.id is not None
        files = await self.files(game) if files is None else files
        attached = await self.attached(game)
        config = await self.bot.guild_configs.get(game.guild_id)
        buttons: list[ui.Item] = [YamlButton(game.id), YamlsZipButton(game.id)]
        if self.bot.settings.archipelago_path:
            buttons.append(CheckButton(game.id))
        return signup_view(
            game,
            files,
            apworlds=attached,
            missing=list(await self.missing(game, files, attached)),
            conflicts=await self.conflicts(game, [p for f in files for p in f.players], attached),
            track_command=self.bot.command_mention("track start"),
            host_command=self.bot.command_mention("partie lancer") if self.bot.settings.host_address else None,
            ping_role_id=config.ping_role_id,
            buttons=buttons,
        )

    async def refresh(self, game: GameRecord) -> None:
        if self.get(game.id or 0) is None:
            return  # started: the post shows the room now
        try:
            await ThreadSink(self.bot, game).edit_panel(await self.render(game))
        except discord.NotFound:
            log.info("The post of game %s was deleted, cancelling it", game.id)
            self._games.pop(game.id or 0, None)
            await self._cancel(game)
        except discord.HTTPException:
            log.warning("Could not update the panel of game %s", game.id, exc_info=True)

    async def create(
        self, forum: discord.ForumChannel, name: str, description: str, starts_at: datetime | None, user_id: int
    ) -> GameRecord:
        game = GameRecord(
            guild_id=forum.guild.id, name=name, description=description, starts_at=starts_at, created_by=user_id
        )
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

    async def edit(self, game: GameRecord, *, name: str, description: str, starts_at: datetime | None) -> None:
        renamed = name != game.name
        game.name, game.description, game.starts_at = name, description, starts_at
        await self.repo.save_details(game)
        if renamed:
            try:
                await (await ThreadSink(self.bot, game).thread()).edit(name=name)
            except discord.HTTPException:
                log.warning("Could not rename the post of game %s", game.id, exc_info=True)
        await self.refresh(game)

    async def upload(
        self, game: GameRecord, user_id: int, attachments: Sequence[discord.Attachment], *, admin: bool = False
    ) -> list[Upload]:
        """`admin`: even after the start, replacing anyone's yaml for them."""
        uploads = []
        for attachment in attachments:
            if is_apworld_filename(attachment.filename):
                uploads.append(await self._upload_apworld(game, user_id, attachment, admin=admin))
                continue
            upload = Upload(attachment.filename)
            try:
                if not is_yaml_filename(attachment.filename):
                    raise YamlError("ce n'est ni un `.yaml` ni un `.apworld`.")
                if attachment.size > MAX_SIZE:
                    raise YamlError(f"fichier trop gros (plus de {MAX_SIZE // 1024} Ko).")
                content = await attachment.read()
                new = read_yaml(user_id, PurePath(attachment.filename).name, content)
                async with self._lock:
                    if self.get(game.id or 0) is None and not admin:
                        raise YamlError("les inscriptions de cette partie sont fermées.")
                    replaced = replaced_by(await self.files(game), new, anyone=admin)
                    if len(owners := {old.user_id for old in replaced}) == 1:
                        new.user_id = owners.pop()
                    await self.repo.replace_yamls(game.id or 0, new, replaced)
            except YamlError as e:
                upload.error = str(e)
            except discord.HTTPException:
                upload.error = "impossible de télécharger le fichier."
            else:
                upload.players = new.players
                upload.invalid = new.invalid
                upload.warnings = warnings(new.players) + await self.conflicts(game, new.players)
                upload.replaced = [old.filename for old in replaced]
            uploads.append(upload)
        if any(u.error is None for u in uploads):
            await self.refresh(game)
        return uploads

    async def _upload_apworld(
        self, game: GameRecord, user_id: int, attachment: discord.Attachment, *, admin: bool = False
    ) -> Upload:
        upload = Upload(attachment.filename)
        try:
            apworld, content = await _read_apworld(attachment, user_id)
            async with self._lock:
                if self.get(game.id or 0) is None and not admin:
                    raise ApworldError("les inscriptions de cette partie sont fermées.")
                apworld = await self.apworlds.add(game.guild_id, apworld, content)
                replaced = await self.apworlds.attach(game.id or 0, apworld, user_id)
        except ApworldError as e:
            upload.error = str(e)
        except discord.HTTPException:
            upload.error = "impossible de télécharger le fichier."
        else:
            upload.apworld = apworld
            upload.replaced = [a.apworld.label for a in replaced]
            players = [p for f in await self.files(game) for p in f.players]
            upload.warnings = [conflict_text(*c) for c in version_conflicts(players, {apworld.game: apworld})]
        return upload

    async def use_apworld(self, game: GameRecord, user_id: int, apworld_id: int) -> Apworld:
        """Pick an apworld this server already has."""
        async with self._lock:
            if self.get(game.id or 0) is None:
                raise UserError("Les inscriptions de cette partie sont fermées.")
            apworld = await self._attach(game, user_id, apworld_id)
        await self.refresh(game)
        return apworld

    async def add_apworld(self, guild_id: int, user_id: int, attachment: discord.Attachment) -> Apworld:
        """Into this server's library, outside any game."""
        apworld, content = await _read_apworld(attachment, user_id)
        return await self.apworlds.add(guild_id, apworld, content)

    async def import_apworlds(self, guild_id: int, messages: Iterable[discord.Message]) -> ApworldImport:
        """The apworlds attached to these messages, oldest first: the library offers the newest first."""
        result = ApworldImport()
        known = await self.apworlds.hashes(guild_id)
        for message in messages:
            for attachment in message.attachments:
                if not is_apworld_filename(attachment.filename):
                    continue
                try:
                    apworld, content = await _read_apworld(attachment, message.author.id)
                except ApworldError as e:
                    result.refused.append((attachment.filename, str(e)))
                    continue
                except discord.HTTPException:
                    result.refused.append((attachment.filename, "impossible de télécharger le fichier."))
                    continue
                if apworld.sha256 in known:
                    result.known += 1
                    continue
                apworld.uploaded_at = message.created_at
                result.added.append(await self.apworlds.add(guild_id, apworld, content))
                known.add(apworld.sha256)
        return result

    async def set_apworld(self, game: GameRecord, user_id: int, apworld_id: int) -> Apworld:
        """After the start: which apworld generated a world, to regenerate the seed."""
        return await self._attach(game, user_id, apworld_id)

    async def _attach(self, game: GameRecord, user_id: int, apworld_id: int) -> Apworld:
        apworld = await self.apworlds.get(game.guild_id, apworld_id)
        if apworld is None:
            raise UserError("Cet apworld n'existe plus.")
        await self.apworlds.attach(game.id or 0, apworld, user_id)
        return apworld

    async def remove_apworld(self, game: GameRecord, user_id: int, apworld_id: int) -> None:
        async with self._lock:
            if not any(a.apworld.id == apworld_id and a.user_id == user_id for a in await self.attached(game)):
                raise UserError("Cet apworld n'est plus là.")
            await self.apworlds.detach(game.id or 0, apworld_id)
        await self.refresh(game)

    async def remove(self, game: GameRecord, user_id: int, yaml_id: int, *, admin: bool = False) -> None:
        async with self._lock:
            files = await self.files(game)
            if not any(f.id == yaml_id and (admin or f.user_id == user_id) for f in files):
                raise UserError("Ce yaml n'est plus là.")
            await self.repo.remove_yaml(yaml_id)
        await self.refresh(game)

    async def entries(
        self, game: GameRecord, *, used_only: bool = False
    ) -> tuple[list[tuple[str, bytes]], list[YamlFile], list[Attached]]:
        """Everything to generate, laid out like the Archipelago folder.

        `used_only`: without the apworlds of games no yaml plays, some change the other worlds when loaded.
        """
        files = await self.files(game)
        attached = await self.attached(game)
        if used_only and None not in (games := {p.game for f in files for p in f.players}):
            attached = [a for a in attached if a.apworld.game in games]
        contents = await self.apworlds.contents(a.apworld.id for a in attached if a.apworld.id is not None)
        entries = [
            (f"Players/{name}", f.content)
            for name, f in zip(unique_filenames([f.filename for f in files]), files, strict=True)
        ]
        entries += [
            (f"custom_worlds/{name}", contents[a.apworld.id or 0])
            for name, a in zip(unique_filenames([a.apworld.filename for a in attached]), attached, strict=True)
        ]
        return entries, files, attached

    async def generation_files(self, thread_id: int, players: Sequence[SlotInfo]) -> list[tuple[str, bytes]] | None:
        """What generated the room tracked in this post, if it was started from a sign-up post.

        The yamls are prefixed so that Generate.py's filename sort numbers their players like the room's slots.
        """
        game = await self.repo.started_in(thread_id)
        if game is None:
            return None
        entries, files, _ = await self.entries(game, used_only=True)
        if not files:
            return None
        yamls = [(f"Players/{i:03d}_{f.filename}", f.content) for i, f in enumerate(generation_order(files, players))]
        return yamls + [e for e in entries if not e[0].startswith("Players/")]

    async def bundle(self, game: GameRecord, limit: int) -> Bundle:
        """Everything to generate, split in zips that fit under `limit`."""
        entries, files, attached = await self.entries(game)
        parts: list[list[tuple[str, bytes]]] = [[]]
        size = 0
        for entry in entries:
            if parts[-1] and size + len(entry[1]) > limit - ZIP_OVERHEAD:
                parts.append([])
                size = 0
            parts[-1].append(entry)
            size += len(entry[1])
        return Bundle([_zip(part) for part in parts if part], len(files), len(attached))

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

    async def cancel(self, game: GameRecord) -> bool:
        async with self._lock:
            if self._games.pop(game.id or 0, None) is None:
                return False
            await self.repo.set_status(game, "cancelled")
        sink = ThreadSink(self.bot, game)
        try:
            await sink.edit_panel(await self.render(game))
            await sink.set_tag(RoomTag.CANCELLED)
        except discord.HTTPException:
            log.warning("Could not update the post of cancelled game %s", game.id, exc_info=True)
        return True

    async def reopen(self, game: GameRecord) -> None:
        """Back to sign-ups after its room was discarded: the yamls are still there."""
        assert game.id is not None
        await self.repo.set_status(game, "open")
        self._games[game.id] = game
        await self.refresh(game)
        try:
            await ThreadSink(self.bot, game).set_tag(RoomTag.SIGNUP)
        except discord.HTTPException:
            log.warning("Could not tag game %s", game.id, exc_info=True)

    def thread_deleted(self, thread_id: int) -> None:
        if game := self.by_thread(thread_id):
            self._games.pop(game.id or 0, None)
            asyncio.create_task(self._cancel(game), name=f"cancel game {game.id}")  # noqa: RUF006

    async def _cancel(self, game: GameRecord) -> None:
        try:
            await self.repo.set_status(game, "cancelled")
        except Exception:
            log.exception("Could not cancel game %s", game.id)


async def _read_apworld(attachment: discord.Attachment, user_id: int) -> tuple[Apworld, bytes]:
    if attachment.size > MAX_APWORLD_SIZE:
        raise ApworldError(f"fichier trop gros (plus de {MAX_APWORLD_SIZE // 1024 // 1024} Mo).")
    content = await attachment.read()
    return read_apworld(content, user_id), content


def _zip(entries: list[tuple[str, bytes]]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, content in entries:
            compression = zipfile.ZIP_STORED if is_apworld_filename(name) else zipfile.ZIP_DEFLATED
            archive.writestr(name, content, compress_type=compression)
    return buffer.getvalue()
