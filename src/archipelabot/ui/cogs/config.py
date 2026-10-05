from typing import TYPE_CHECKING

import discord
from discord import app_commands
from discord.ext import commands

from ...errors import UserError
from ..components import Tone, notice
from ..emojis import E
from ..forum import TAG_SPECS, ensure_room_tags, missing_permissions
from ..types import Interaction

if TYPE_CHECKING:
    from ...bot import ArchipelaBot


@app_commands.guild_only()
@app_commands.default_permissions(manage_guild=True)
class ConfigCog(
    commands.GroupCog, name="config", group_name="config", group_description="Configurer le bot sur ce serveur"
):
    def __init__(self, bot: "ArchipelaBot") -> None:
        self.bot = bot

    @app_commands.command(name="forum", description="Choisir le salon forum où chaque room suivie aura son post")
    @app_commands.describe(salon="Un salon de type forum")
    async def set_forum(self, interaction: Interaction, salon: discord.ForumChannel) -> None:
        if missing := missing_permissions(salon):
            raise UserError(
                f"Il me manque des permissions dans {salon.mention} : " + ", ".join(f"`{p}`" for p in missing)
            )
        await interaction.response.defer(ephemeral=True)
        await ensure_room_tags(salon)

        config = await self.bot.guild_configs.get(salon.guild.id)
        config.forum_id = salon.id
        await self.bot.guild_configs.save(config)

        tags = " ".join(f"{emoji} {name}" for name, emoji in TAG_SPECS.values())
        await interaction.followup.send(
            view=notice(f"Les rooms suivies seront postées dans {salon.mention}.\n-# Tags : {tags}", Tone.SUCCESS),
            ephemeral=True,
        )

    @app_commands.command(name="recap", description="Choisir un salon où publier aussi les récaps de fin de partie")
    @app_commands.describe(salon="Laisser vide pour publier les récaps uniquement dans le post de la room")
    async def set_recap(self, interaction: Interaction, salon: discord.TextChannel | None = None) -> None:
        config = await self.bot.guild_configs.get(interaction.guild_id)
        config.recap_channel_id = salon.id if salon else None
        await self.bot.guild_configs.save(config)
        text = (
            f"Les récaps seront aussi publiés dans {salon.mention}."
            if salon
            else "Les récaps seront publiés uniquement dans le post de chaque room."
        )
        await interaction.response.send_message(view=notice(text, Tone.SUCCESS), ephemeral=True)

    @app_commands.command(name="voir", description="Afficher la configuration actuelle")
    async def show(self, interaction: Interaction) -> None:
        config = await self.bot.guild_configs.get(interaction.guild_id)
        forum = f"<#{config.forum_id}>" if config.forum_id else "*non configuré* — utilise `/config forum`"
        recap = f"<#{config.recap_channel_id}>" if config.recap_channel_id else "*post de la room uniquement*"
        await interaction.response.send_message(
            view=notice(f"### {E.settings} Configuration\n**Forum des rooms** : {forum}\n**Récaps** : {recap}"),
            ephemeral=True,
        )


async def setup(bot: "ArchipelaBot") -> None:
    await bot.add_cog(ConfigCog(bot))
