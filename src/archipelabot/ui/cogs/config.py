from typing import TYPE_CHECKING

from discord import app_commands
from discord.ext import commands

from ..config import ConfigView
from ..types import Interaction

if TYPE_CHECKING:
    from ...bot import ArchipelaBot


class ConfigCog(commands.Cog, name="config"):
    def __init__(self, bot: "ArchipelaBot") -> None:
        self.bot = bot

    @app_commands.command(name="config", description="Configurer le bot sur ce serveur")
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_guild=True)
    async def config(self, interaction: Interaction) -> None:
        view = await ConfigView.create(self.bot, interaction.guild_id)  # type: ignore[arg-type]
        await interaction.response.send_message(view=view, ephemeral=True)


async def setup(bot: "ArchipelaBot") -> None:
    await bot.add_cog(ConfigCog(bot))
