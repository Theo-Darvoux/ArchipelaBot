from typing import TYPE_CHECKING

import discord
from discord import app_commands
from discord.ext import commands

from ...errors import UserError
from ..components import Tone, notice
from ..emojis import E
from ..forum import TAG_SPECS, ensure_room_tags, missing_permissions
from ..render.text import md, plural
from ..types import Interaction

if TYPE_CHECKING:
    from ...bot import ArchipelaBot

APWORLD_PERMISSIONS = ("view_channel", "read_message_history", "add_reactions")


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

    @app_commands.command(name="annonces", description="Choisir le rôle pingé quand une partie est annoncée")
    @app_commands.describe(role="Laisser vide pour ne pinger personne")
    async def set_ping_role(self, interaction: Interaction, role: discord.Role | None = None) -> None:
        config = await self.bot.guild_configs.get(interaction.guild_id)
        config.ping_role_id = role.id if role else None
        await self.bot.guild_configs.save(config)
        text = (
            f"{role.mention} sera pingé à l'annonce d'une partie."
            if role
            else "Personne ne sera pingé à l'annonce d'une partie."
        )
        await interaction.response.send_message(view=notice(text, Tone.SUCCESS), ephemeral=True)

    @app_commands.command(name="apworlds", description="Choisir le salon où vous postez vos apworlds")
    @app_commands.describe(salon="Laisser vide pour ne plus suivre de salon : les apworlds déjà repris restent")
    async def set_apworlds(self, interaction: Interaction, salon: discord.TextChannel | None = None) -> None:
        config = await self.bot.guild_configs.get(interaction.guild_id)
        config.apworld_channel_id = salon.id if salon else None
        if salon is None:
            await self.bot.guild_configs.save(config)
            text = "Plus aucun salon d'apworlds suivi. Les apworlds déjà repris restent sur le serveur."
            await interaction.response.send_message(view=notice(text, Tone.SUCCESS), ephemeral=True)
            return
        granted = salon.permissions_for(salon.guild.me)
        if missing := [p for p in APWORLD_PERMISSIONS if not getattr(granted, p)]:
            raise UserError(
                f"Il me manque des permissions dans {salon.mention} : " + ", ".join(f"`{p}`" for p in missing)
            )
        await interaction.response.defer(ephemeral=True, thinking=True)
        await self.bot.guild_configs.save(config)
        result = await self.bot.games.import_apworlds(
            salon.guild.id, [m async for m in salon.history(limit=None, oldest_first=True)]
        )
        text = (
            f"{E.apworld} Apworlds de {salon.mention} repris : {plural(len(result.added), 'nouveau', 'nouveaux')}"
            f", {result.known} déjà sur le serveur.\n-# Ceux postés ensuite dans ce salon seront repris "
            "automatiquement."
        )
        if result.refused:
            text += "\n⚠️ Ignorés :\n" + "\n".join(f"`{md(name)}` : {why}" for name, why in result.refused[:10])
        tone = Tone.WARNING if result.refused else Tone.SUCCESS
        await interaction.followup.send(view=notice(text, tone), ephemeral=True)

    @app_commands.command(name="voir", description="Afficher la configuration actuelle")
    async def show(self, interaction: Interaction) -> None:
        config = await self.bot.guild_configs.get(interaction.guild_id)
        forum = f"<#{config.forum_id}>" if config.forum_id else "*non configuré* — utilise `/config forum`"
        recap = f"<#{config.recap_channel_id}>" if config.recap_channel_id else "*post de la room uniquement*"
        role = f"<@&{config.ping_role_id}>" if config.ping_role_id else "*personne*"
        apworlds = f"<#{config.apworld_channel_id}>" if config.apworld_channel_id else "*aucun*"
        text = (
            f"### {E.settings} Configuration\n**Forum des rooms** : {forum}\n**Récaps** : {recap}\n"
            f"**Pingé pour les nouvelles parties** : {role}\n**Salon des apworlds** : {apworlds}"
        )
        await interaction.response.send_message(view=notice(text), ephemeral=True)


async def setup(bot: "ArchipelaBot") -> None:
    await bot.add_cog(ConfigCog(bot))
