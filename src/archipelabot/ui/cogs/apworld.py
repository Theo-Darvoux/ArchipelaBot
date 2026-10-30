import logging
from typing import TYPE_CHECKING

import discord
from discord import app_commands
from discord.ext import commands

from ...core.apworlds import ApworldError, is_apworld_filename
from ...errors import UserError
from ..components import Tone, notice
from ..emojis import E
from ..render.signup import apworld_label
from ..render.text import code
from ..types import Interaction

if TYPE_CHECKING:
    from ...bot import ArchipelaBot

log = logging.getLogger(__name__)


@app_commands.guild_only()
class ApworldCog(commands.GroupCog, name="apworld", group_name="apworld", group_description="Apworlds du serveur"):
    def __init__(self, bot: "ArchipelaBot") -> None:
        self.bot = bot

    @app_commands.command(name="envoyer", description="Ajouter un apworld au serveur, hors d'une partie")
    @app_commands.describe(fichier="Le fichier .apworld")
    async def send(self, interaction: Interaction, fichier: discord.Attachment) -> None:
        assert interaction.guild_id is not None
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            apworld = await self.bot.games.add_apworld(interaction.guild_id, interaction.user.id, fichier)
        except ApworldError as e:
            raise UserError(f"Apworld refusé : {e}") from e
        except discord.HTTPException as e:
            raise UserError("Impossible de télécharger le fichier.") from e
        text = (
            f"{E.apworld} {apworld_label(apworld)} est sur le serveur.\n-# Il sera proposé dans les inscriptions, et "
            f"dans {E.settings} **Gérer** puis **Apworlds** sur le panneau d'une partie déjà lancée."
        )
        await interaction.followup.send(view=notice(text, Tone.SUCCESS), ephemeral=True)

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if message.author.bot or message.guild is None:
            return
        if not any(is_apworld_filename(a.filename) for a in message.attachments):
            return
        if (await self.bot.guild_configs.get(message.guild.id)).apworld_channel_id != message.channel.id:
            return
        result = await self.bot.games.import_apworlds(message.guild.id, [message])
        try:
            if result.refused:
                text = "⚠️ Apworld ignoré :\n" + "\n".join(f"{code(name)} : {why}" for name, why in result.refused)
                await message.reply(view=notice(text, Tone.WARNING), mention_author=False)
            else:
                await message.add_reaction("✅")
        except discord.HTTPException:
            log.warning("Could not answer an apworld in channel %s", message.channel.id, exc_info=True)


async def setup(bot: "ArchipelaBot") -> None:
    await bot.add_cog(ApworldCog(bot))
