import asyncio
import hashlib
import json
import logging

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands

from .ap.datapackage import DataPackageStore
from .ap.webhost import WebhostClient
from .config import Settings
from .errors import UserError
from .storage.claims import NotifPrefs
from .storage.db import Database
from .storage.guilds import GuildRepo
from .ui.components import Tone, notice
from .ui.emojis import ensure_uploaded
from .ui.games import GameManager
from .ui.panel_buttons import ClaimButton, MyHintsButton, SettingsButton
from .ui.rooms import RoomManager
from .ui.signup_buttons import YamlButton, YamlsZipButton

log = logging.getLogger(__name__)

BACKUP_INTERVAL = 24 * 3600
BACKUPS_KEPT = 7

EXTENSIONS = (
    "archipelabot.ui.cogs.config",
    "archipelabot.ui.cogs.track",
    "archipelabot.ui.cogs.game",
    "archipelabot.ui.cogs.claim",
    "archipelabot.ui.cogs.status",
    "archipelabot.ui.cogs.bridge",
    "archipelabot.ui.cogs.help",
)


class Tree(app_commands.CommandTree["ArchipelaBot"]):
    async def on_error(
        self, interaction: discord.Interaction["ArchipelaBot"], error: app_commands.AppCommandError
    ) -> None:
        if isinstance(error, UserError):
            text = f"{error}"
        elif isinstance(error, app_commands.MissingPermissions):
            text = "Tu n'as pas les permissions nécessaires pour cette commande."
        elif isinstance(error, app_commands.CheckFailure):
            text = "Cette commande n'est pas disponible ici."
        else:
            log.exception(
                "Unhandled error in /%s", interaction.command and interaction.command.qualified_name, exc_info=error
            )
            text = "Une erreur inattendue est survenue. Elle a été enregistrée dans les logs."

        view = notice(text, Tone.ERROR)
        if interaction.response.is_done():
            await interaction.followup.send(view=view, ephemeral=True)
        else:
            await interaction.response.send_message(view=view, ephemeral=True)


class ArchipelaBot(commands.Bot):
    def __init__(self, settings: Settings, db: Database) -> None:
        intents = discord.Intents.default()
        intents.message_content = True
        super().__init__(
            command_prefix=commands.when_mentioned,
            help_command=None,
            intents=intents,
            activity=discord.Game("/help"),
            tree_cls=Tree,
            allowed_mentions=discord.AllowedMentions.none(),
        )
        self.settings = settings
        self.db = db
        self.guild_configs = GuildRepo(db)
        self.notif_prefs = NotifPrefs(db)
        self.datapackages = DataPackageStore(settings.database_path.parent / "datapackage")
        self.rooms = RoomManager(self)
        self.games = GameManager(self)
        self.http_session: aiohttp.ClientSession | None = None
        self.command_ids: dict[str, int] = {}
        self._backups: asyncio.Task[None] | None = None

    @property
    def webhost(self) -> WebhostClient:
        assert self.http_session is not None, "setup_hook has not run"
        return WebhostClient(self.http_session)

    async def setup_hook(self) -> None:
        self.http_session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=20))
        await self.notif_prefs.load()
        try:
            await ensure_uploaded(self)
        except discord.HTTPException:
            log.warning("Could not upload the bot's icons; falling back to Unicode emojis", exc_info=True)
        self.add_dynamic_items(ClaimButton, MyHintsButton, SettingsButton, YamlButton, YamlsZipButton)
        await self.load_extensions()
        await self.sync_commands()
        await self.rooms.restore()
        await self.games.restore()
        self._backups = asyncio.create_task(self.backup_loop(), name="backups")

    async def close(self) -> None:
        if self._backups:
            self._backups.cancel()
        await self.rooms.shutdown()
        if self.http_session:
            await self.http_session.close()
        await super().close()

    async def backup_loop(self) -> None:
        directory = self.settings.database_path.parent / "backups"
        while True:
            try:
                log.info("Backed up the database to %s", await self.db.backup(directory, BACKUPS_KEPT))
            except Exception:
                log.exception("Could not back up the database")
            await asyncio.sleep(BACKUP_INTERVAL)

    async def load_extensions(self) -> None:
        for extension in EXTENSIONS:
            await self.load_extension(extension)

    async def sync_commands(self) -> None:
        guild = discord.Object(self.settings.dev_guild_id) if self.settings.dev_guild_id else None
        if guild:
            self.tree.copy_global_to(guild=guild)
        payload = [command.to_dict(self.tree) for command in self.tree.get_commands(guild=guild)]
        digest = hashlib.sha256(json.dumps([guild and guild.id, payload], sort_keys=True).encode()).hexdigest()
        marker = self.settings.database_path.parent / "commands.sha256"
        where = f"dev guild {guild.id}" if guild else "global"
        if marker.exists() and marker.read_text() == digest:
            commands = await self.tree.fetch_commands(guild=guild)
            log.info("Commands unchanged (%s), not syncing", where)
        else:
            commands = await self.tree.sync(guild=guild)
            marker.parent.mkdir(parents=True, exist_ok=True)
            marker.write_text(digest)
            log.info("Synced %d commands (%s)", len(commands), where)
        self.command_ids = {command.name: command.id for command in commands}

    def command_mention(self, name: str) -> str:
        command_id = self.command_ids.get(name.split()[0])
        return f"</{name}:{command_id}>" if command_id else f"`/{name}`"

    async def on_ready(self) -> None:
        log.info("Logged in as %s (%d guilds)", self.user, len(self.guilds))

    async def on_raw_thread_delete(self, payload: discord.RawThreadDeleteEvent) -> None:
        self.rooms.thread_deleted(payload.thread_id)
        self.games.thread_deleted(payload.thread_id)
