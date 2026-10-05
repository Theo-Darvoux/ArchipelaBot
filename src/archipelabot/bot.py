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
from .ui.panel_buttons import ClaimButton, MyHintsButton, SettingsButton
from .ui.rooms import RoomManager

log = logging.getLogger(__name__)

EXTENSIONS = (
    "archipelabot.ui.cogs.config",
    "archipelabot.ui.cogs.track",
    "archipelabot.ui.cogs.claim",
    "archipelabot.ui.cogs.status",
    "archipelabot.ui.cogs.bridge",
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
            tree_cls=Tree,
            allowed_mentions=discord.AllowedMentions.none(),
        )
        self.settings = settings
        self.db = db
        self.guild_configs = GuildRepo(db)
        self.notif_prefs = NotifPrefs(db)
        self.datapackages = DataPackageStore(settings.database_path.parent / "datapackage")
        self.rooms = RoomManager(self)
        self.http_session: aiohttp.ClientSession | None = None

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
        self.add_dynamic_items(ClaimButton, MyHintsButton, SettingsButton)
        await self.load_extensions()
        await self.sync_commands()
        await self.rooms.restore()

    async def close(self) -> None:
        await self.rooms.shutdown()
        if self.http_session:
            await self.http_session.close()
        await super().close()

    async def load_extensions(self) -> None:
        for extension in EXTENSIONS:
            await self.load_extension(extension)

    async def sync_commands(self) -> None:
        if self.settings.dev_guild_id:
            guild = discord.Object(self.settings.dev_guild_id)
            self.tree.copy_global_to(guild=guild)
            synced = await self.tree.sync(guild=guild)
            log.info("Synced %d commands to dev guild %d", len(synced), guild.id)
        else:
            synced = await self.tree.sync()
            log.info("Synced %d global commands", len(synced))

    async def on_ready(self) -> None:
        log.info("Logged in as %s (%d guilds)", self.user, len(self.guilds))
