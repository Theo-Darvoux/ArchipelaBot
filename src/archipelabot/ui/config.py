from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any

import discord
from discord import ui

from ..errors import UserError
from ..storage.guilds import GuildConfig
from .components import Tone, View
from .emojis import E
from .forum import ensure_room_tags, missing_permissions
from .render.text import code, plural

if TYPE_CHECKING:
    from ..bot import ArchipelaBot
    from .types import Interaction

APWORLD_PERMISSIONS = ("view_channel", "read_message_history", "add_reactions")
LOG_PERMISSIONS = (
    "view_channel",
    "send_messages",
    "create_public_threads",
    "send_messages_in_threads",
    "manage_threads",
    "read_message_history",
)
TEXT_CHANNELS = [discord.ChannelType.text, discord.ChannelType.news]

type Apply = Callable[[Any], Awaitable[str]]


def check_permissions(channel: Any, needed: tuple[str, ...] | list[str]) -> None:
    granted = channel.permissions_for(channel.guild.me)
    if missing := [p for p in needed if not getattr(granted, p)]:
        raise UserError(
            f"Il me manque des permissions dans {channel.mention} : " + ", ".join(f"`{p}`" for p in missing)
        )


class ConfigView(View):
    """Every server setting on one panel, each changed with its own menu."""

    def __init__(self, bot: "ArchipelaBot", config: GuildConfig) -> None:
        super().__init__(timeout=900)
        self.bot = bot
        self.config = config
        self.note = ""
        self.tone = Tone.INFO
        self._build()

    @classmethod
    async def create(cls, bot: "ArchipelaBot", guild_id: int) -> "ConfigView":
        return cls(bot, await bot.guild_configs.get(guild_id))

    def _build(self) -> None:
        self.clear_items()
        c = self.config
        title = f"### {E.settings} Configuration" + (f"\n{self.note}" if self.note else "")
        container = ui.Container(ui.TextDisplay(title), accent_colour=self.tone.value)
        self._channel(
            container, "Forum des parties", "Chaque partie y a son post : inscriptions, puis suivi de la room.",
            c.forum_id, [discord.ChannelType.forum], self._set_forum, required=True,
        )  # fmt: skip
        self._channel(
            container, "Suivi complet", "Forum en lecture seule : tous les checks de chaque room.",
            c.log_forum_id, [discord.ChannelType.forum], self._set_log_forum,
        )  # fmt: skip
        self._channel(
            container, "Récaps", "Salon où publier aussi les récaps de fin de partie.",
            c.recap_channel_id, TEXT_CHANNELS, self._set_recap,
        )  # fmt: skip
        self._channel(
            container, "Apworlds", "Salon où vous postez vos apworlds : le bot les reprend pour les proposer.",
            c.apworld_channel_id, TEXT_CHANNELS, self._set_apworlds,
        )  # fmt: skip

        role = f"<@&{c.ping_role_id}>" if c.ping_role_id else "*personne*"
        container.add_item(ui.TextDisplay(f"**Annonces** · {role}\n-# Rôle pingé quand une partie est annoncée."))
        roles = ui.RoleSelect(
            placeholder="Personne",
            min_values=0,
            default_values=[discord.Object(c.ping_role_id)] if c.ping_role_id else [],
        )
        roles.callback = self._on_select(roles, self._set_role)
        container.add_item(ui.ActionRow(roles))

        container.add_item(ui.TextDisplay("**Création des parties**\n-# Qui peut lancer `/partie nouvelle`."))
        creation = ui.Select(
            options=[
                discord.SelectOption(label="Tous les membres", value="all", default=not c.admin_only_games),
                discord.SelectOption(
                    label="Admins uniquement", value="admins", description="Permission Gérer le serveur",
                    default=c.admin_only_games,
                ),
            ]
        )  # fmt: skip
        creation.callback = self._on_select(creation, self._set_creation)
        container.add_item(ui.ActionRow(creation))
        self.add_item(container)

    def _channel(
        self,
        container: ui.Container,
        label: str,
        help_text: str,
        current: int | None,
        types: list[discord.ChannelType],
        apply: Apply,
        *,
        required: bool = False,
    ) -> None:
        shown = f"<#{current}>" if current else "*aucun*"
        container.add_item(ui.TextDisplay(f"**{label}** · {shown}\n-# {help_text}"))
        select = ui.ChannelSelect(
            channel_types=types,
            placeholder="Choisir un salon" if required else "Aucun",
            min_values=1 if required else 0,
            default_values=[discord.Object(current)] if current else [],
        )
        select.callback = self._on_select(select, apply)
        container.add_item(ui.ActionRow(select))

    def _on_select(self, select: Any, apply: Apply) -> Callable[["Interaction"], Awaitable[None]]:
        async def callback(interaction: "Interaction") -> None:
            await interaction.response.defer()
            value = select.values[0] if select.values else None
            if isinstance(select, ui.ChannelSelect) and value is not None and interaction.guild is not None:
                value = interaction.guild.get_channel(value.id) or value
            try:
                self.note = await apply(value)
                self.tone = Tone.SUCCESS
                await self.bot.guild_configs.save(self.config)
            except UserError as e:
                self.config = await self.bot.guild_configs.get(self.config.guild_id)
                self.note, self.tone = f"⚠️ {e}", Tone.ERROR
            self._build()
            await interaction.edit_original_response(view=self)

        return callback

    async def _set_forum(self, forum: Any) -> str:
        if missing := missing_permissions(forum):
            raise UserError(
                f"Il me manque des permissions dans {forum.mention} : " + ", ".join(f"`{p}`" for p in missing)
            )
        await ensure_room_tags(forum)
        self.config.forum_id = forum.id
        return f"Les parties seront postées dans {forum.mention}."

    async def _set_log_forum(self, forum: Any) -> str:
        if forum is None:
            self.config.log_forum_id = None
            return "Plus de suivi complet : seuls `/historique` et le post de la room restent."
        check_permissions(forum, LOG_PERMISSIONS)
        self.config.log_forum_id = forum.id
        return (
            f"Chaque room aura son suivi complet dans {forum.mention}.\n-# Pour le mettre en lecture seule, retire "
            "aux membres *Envoyer des messages* et *Envoyer des messages dans les fils* sur ce forum."
        )

    async def _set_recap(self, channel: Any) -> str:
        self.config.recap_channel_id = channel.id if channel else None
        if channel is None:
            return "Les récaps seront publiés uniquement dans le post de chaque room."
        return f"Les récaps seront aussi publiés dans {channel.mention}."

    async def _set_role(self, role: Any) -> str:
        self.config.ping_role_id = role.id if role else None
        return f"{role.mention} sera pingé à l'annonce d'une partie." if role else "Personne ne sera pingé."

    async def _set_creation(self, value: str) -> str:
        self.config.admin_only_games = value == "admins"
        if self.config.admin_only_games:
            return "Seuls les admins (permission *Gérer le serveur*) peuvent créer des parties."
        return "Tous les membres peuvent créer des parties."

    async def _set_apworlds(self, channel: Any) -> str:
        if channel is None:
            self.config.apworld_channel_id = None
            return "Plus aucun salon d'apworlds suivi. Les apworlds déjà repris restent sur le serveur."
        check_permissions(channel, APWORLD_PERMISSIONS)
        self.config.apworld_channel_id = channel.id
        result = await self.bot.games.import_apworlds(
            channel.guild.id, [m async for m in channel.history(limit=None, oldest_first=True)]
        )
        text = (
            f"{E.apworld} Apworlds de {channel.mention} repris : {plural(len(result.added), 'nouveau', 'nouveaux')}"
            f", {result.known} déjà sur le serveur.\n-# Ceux postés ensuite dans ce salon seront repris "
            "automatiquement."
        )
        if result.refused:
            text += "\n⚠️ Ignorés :\n" + "\n".join(f"{code(name)} : {why}" for name, why in result.refused[:10])
        return text
