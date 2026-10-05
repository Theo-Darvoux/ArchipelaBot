from typing import TYPE_CHECKING

from discord import app_commands
from discord.ext import commands

from ..render.help import help_view
from ..types import Interaction

if TYPE_CHECKING:
    from ...bot import ArchipelaBot


class HelpCog(commands.Cog, name="help"):
    def __init__(self, bot: "ArchipelaBot") -> None:
        self.bot = bot

    @app_commands.command(name="help", description="Comment fonctionne le bot et ce que fait chaque commande")
    async def help(self, interaction: Interaction) -> None:
        await interaction.response.send_message(view=help_view(self.bot.command_mention), ephemeral=True)


async def setup(bot: "ArchipelaBot") -> None:
    await bot.add_cog(HelpCog(bot))
