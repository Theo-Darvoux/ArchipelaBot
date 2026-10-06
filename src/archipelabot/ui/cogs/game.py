import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

import discord
from discord import app_commands
from discord.ext import commands

from ...core.schedule import ScheduleError, label, parse_start, suggestions
from ...core.yamls import is_yaml_filename
from ...errors import UserError
from ...storage.games import GameRecord
from ..components import Tone, notice
from ..emojis import E
from ..forum import TAG_SPECS, RoomTag
from ..render.signup import upload_report
from ..render.text import md
from ..types import Interaction
from ..views import ConfirmView

if TYPE_CHECKING:
    from ...bot import ArchipelaBot

log = logging.getLogger(__name__)

CLEAR = "-"


@app_commands.guild_only()
class GameCog(commands.GroupCog, name="partie", group_name="partie", group_description="Organiser une partie"):
    def __init__(self, bot: "ArchipelaBot") -> None:
        self.bot = bot

    def _now(self) -> datetime:
        return datetime.now(ZoneInfo(self.bot.settings.timezone))

    def _start(self, text: str) -> datetime:
        try:
            return parse_start(text, self._now()).astimezone(UTC)
        except ScheduleError as e:
            raise UserError(f"Début de partie : {e}") from e

    async def start_autocomplete(self, _interaction: Interaction, current: str) -> list[app_commands.Choice[str]]:
        choices = [
            app_commands.Choice(name=label(start), value=label(start)) for start in suggestions(current, self._now())
        ]
        if current.strip() == CLEAR:
            choices.append(app_commands.Choice(name="Retirer la date", value=CLEAR))
        return choices

    @app_commands.command(name="nouvelle", description="Annoncer une partie et ouvrir les inscriptions (yamls)")
    @app_commands.describe(
        nom="Nom du post dans le forum",
        debut="Date et heure de début annoncées, par exemple « samedi 21h » ou « 12/10 20h30 »",
        description="Infos pour les joueurs : règles, vocal… (affiché dans le post)",
    )
    @app_commands.autocomplete(debut=start_autocomplete)
    async def new(
        self,
        interaction: Interaction,
        nom: app_commands.Range[str, 1, 90] | None = None,
        debut: str | None = None,
        description: app_commands.Range[str, 1, 1000] | None = None,
    ) -> None:
        guild = interaction.guild
        assert guild is not None
        starts_at = self._start(debut) if debut else None
        config = await self.bot.guild_configs.get(guild.id)
        forum = guild.get_channel(config.forum_id) if config.forum_id else None
        if forum is None or forum.type != discord.ChannelType.forum:
            raise UserError("Aucun forum configuré pour les parties. Un admin doit d'abord utiliser `/config forum`.")
        await interaction.response.defer(ephemeral=True, thinking=True)
        name = nom or f"Multiworld du {(starts_at or datetime.now(UTC)).astimezone(self._now().tzinfo):%d/%m}"
        game = await self.bot.games.create(
            forum,  # type: ignore[arg-type]
            name,
            description or "",
            starts_at,
            interaction.user.id,
        )
        text = f"Partie annoncée → <#{game.thread_id}>\n-# Les inscriptions sont ouvertes : chacun envoie son yaml."
        await interaction.followup.send(view=notice(text, Tone.SUCCESS), ephemeral=True)

    @app_commands.command(name="modifier", description="Changer le nom, la date ou la description de cette partie")
    @app_commands.describe(
        nom="Nouveau nom du post",
        debut="Nouvelle date et heure de début (« - » pour la retirer)",
        description="Nouvelle description (« - » pour la retirer)",
    )
    @app_commands.autocomplete(debut=start_autocomplete)
    async def edit(
        self,
        interaction: Interaction,
        nom: app_commands.Range[str, 1, 90] | None = None,
        debut: str | None = None,
        description: app_commands.Range[str, 1, 1000] | None = None,
    ) -> None:
        game = self._game_here(interaction, "la modifier")
        starts_at = game.starts_at
        if debut is not None:
            starts_at = None if debut.strip() == CLEAR else self._start(debut)
        if description is not None:
            description = "" if description.strip() == CLEAR else description
        await self.bot.games.edit(
            game,
            name=nom or game.name,
            description=game.description if description is None else description,
            starts_at=starts_at,
        )
        await interaction.response.send_message(view=notice("Partie mise à jour.", Tone.SUCCESS), ephemeral=True)

    @app_commands.command(name="annuler", description="Annuler cette partie : les inscriptions sont fermées")
    async def cancel(self, interaction: Interaction) -> None:
        game = self._game_here(interaction, "l'annuler")

        async def cancel() -> str:
            if not await self.bot.games.cancel(game):
                return "Cette partie n'est plus en inscriptions."
            return f"{E.stopped} Partie annulée. Le post reste consultable, avec le tag {cancelled_tag}."

        cancelled_tag = " ".join(TAG_SPECS[RoomTag.CANCELLED][::-1])
        view = ConfirmView(
            f"Annuler **{md(game.name)}** ?\n-# Les inscriptions seront fermées et le post gardera la liste des "
            f"inscrits, avec le tag {cancelled_tag}.",
            [("Annuler la partie", discord.ButtonStyle.danger, cancel)],
            cancel_label="Garder la partie",
        )
        await interaction.response.send_message(view=view, ephemeral=True)

    def _game_here(self, interaction: Interaction, action: str) -> GameRecord:
        game = self.bot.games.by_thread(interaction.channel_id)
        if game is None:
            raise UserError("Utilise cette commande dans le post d'une partie en inscriptions.")
        if interaction.user.id != game.created_by and not interaction.permissions.manage_threads:
            raise UserError(f"Seule la personne qui a annoncé la partie (ou un modérateur) peut {action}.")
        return game

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if message.author.bot or message.guild is None:
            return
        game = self.bot.games.by_thread(message.channel.id)
        if game is not None and (yamls := [a for a in message.attachments if is_yaml_filename(a.filename)]):
            await self._yamls(game, message, yamls)

    async def _yamls(self, game: GameRecord, message: discord.Message, attachments: list[discord.Attachment]) -> None:
        uploads = await self.bot.games.upload(game, message.author.id, attachments)
        try:
            if all(u.clean for u in uploads):
                await message.add_reaction("✅")
            else:
                await message.reply(view=upload_report(uploads), mention_author=False)
        except discord.HTTPException:
            log.warning("Could not answer a yaml upload in game %s", game.id, exc_info=True)


async def setup(bot: "ArchipelaBot") -> None:
    await bot.add_cog(GameCog(bot))
