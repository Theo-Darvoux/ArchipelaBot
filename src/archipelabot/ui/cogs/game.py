import logging
import re
from datetime import datetime
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

import discord
from discord import app_commands
from discord.ext import commands

from ...core.yamls import is_yaml_filename
from ...errors import UserError
from ...storage.games import GameRecord
from ..components import Tone, notice
from ..launch import launch_room
from ..render.signup import upload_report
from ..types import Interaction

if TYPE_CHECKING:
    from ...bot import ArchipelaBot

log = logging.getLogger(__name__)

ROOM_LINK = re.compile(r"https?://[\w.-]+(?::\d+)?/room/[\w-]+")


@app_commands.guild_only()
class GameCog(commands.GroupCog, name="partie", group_name="partie", group_description="Organiser une partie"):
    def __init__(self, bot: "ArchipelaBot") -> None:
        self.bot = bot

    @app_commands.command(name="nouvelle", description="Annoncer une partie et ouvrir les inscriptions (yamls)")
    @app_commands.describe(
        nom="Nom du post dans le forum", description="Quand, comment, règles… (affiché dans le post)"
    )
    async def new(
        self,
        interaction: Interaction,
        nom: app_commands.Range[str, 1, 90] | None = None,
        description: app_commands.Range[str, 1, 1000] | None = None,
    ) -> None:
        guild = interaction.guild
        assert guild is not None
        config = await self.bot.guild_configs.get(guild.id)
        forum = guild.get_channel(config.forum_id) if config.forum_id else None
        if forum is None or forum.type != discord.ChannelType.forum:
            raise UserError("Aucun forum configuré pour les parties. Un admin doit d'abord utiliser `/config forum`.")
        await interaction.response.defer(ephemeral=True, thinking=True)
        today = datetime.now(ZoneInfo(self.bot.settings.timezone))
        game = await self.bot.games.create(
            forum,  # type: ignore[arg-type]
            nom or f"Multiworld du {today:%d/%m}",
            description or "",
            interaction.user.id,
        )
        text = f"Partie annoncée → <#{game.thread_id}>\n-# Les inscriptions sont ouvertes : chacun envoie son yaml."
        await interaction.followup.send(view=notice(text, Tone.SUCCESS), ephemeral=True)

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if message.author.bot or message.guild is None:
            return
        game = self.bot.games.by_thread(message.channel.id)
        if game is None:
            return
        if yamls := [a for a in message.attachments if is_yaml_filename(a.filename)]:
            await self._yamls(game, message, yamls)
        if link := ROOM_LINK.search(message.content):
            await self._start(game, message, link[0])

    async def _yamls(self, game: GameRecord, message: discord.Message, attachments: list[discord.Attachment]) -> None:
        uploads = await self.bot.games.upload(game, message.author.id, attachments)
        try:
            if all(u.clean for u in uploads):
                await message.add_reaction("✅")
            else:
                await message.reply(view=upload_report(uploads), mention_author=False)
        except discord.HTTPException:
            log.warning("Could not answer a yaml upload in game %s", game.id, exc_info=True)

    async def _start(self, game: GameRecord, message: discord.Message, link: str) -> None:
        assert message.guild is not None
        try:
            async with message.channel.typing():
                await launch_room(self.bot, message.guild, link, user_id=message.author.id, game=game)
        except UserError as e:
            text = f"{e}\n-# Pour donner un mot de passe ou un slot, utilise `/track start` dans ce post."
            await message.reply(view=notice(text, Tone.ERROR), mention_author=False)
        except Exception:
            log.exception("Could not start game %s from a link", game.id)
            text = "Une erreur inattendue est survenue. Elle a été enregistrée dans les logs."
            await message.reply(view=notice(text, Tone.ERROR), mention_author=False)


async def setup(bot: "ArchipelaBot") -> None:
    await bot.add_cog(GameCog(bot))
