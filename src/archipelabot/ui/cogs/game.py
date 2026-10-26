import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

import discord
from discord import app_commands
from discord.ext import commands

from ...core.hosting import ServerOptions
from ...core.schedule import ScheduleError, label, parse_start, suggestions
from ...errors import UserError
from ...storage.games import GameRecord
from ..apworlds import GameYamlsView
from ..components import Tone, notice
from ..emojis import E
from ..forum import TAG_SPECS, RoomTag
from ..games import check_can_create, is_signup_file
from ..launch import host_game
from ..render.text import md
from ..signup_buttons import UploadReport
from ..types import Interaction
from ..views import ConfirmView

if TYPE_CHECKING:
    from ...bot import ArchipelaBot

log = logging.getLogger(__name__)

CLEAR = "-"
DEFAULTS = ServerOptions()
MODE_CHOICES = [
    app_commands.Choice(name="automatique à l'objectif", value="auto"),
    app_commands.Choice(name="automatique à l'objectif, et à la demande", value="auto-enabled"),
    app_commands.Choice(name="à la demande, après l'objectif", value="goal"),
    app_commands.Choice(name="à la demande, à tout moment", value="enabled"),
    app_commands.Choice(name="jamais", value="disabled"),
]
REMAINING_CHOICES = [
    app_commands.Choice(name="après l'objectif", value="goal"),
    app_commands.Choice(name="à tout moment", value="enabled"),
    app_commands.Choice(name="jamais", value="disabled"),
]


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
        config = await self.bot.guild_configs.get(guild.id)
        check_can_create(config, interaction.permissions)
        starts_at = self._start(debut) if debut else None
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

    @app_commands.command(name="lancer", description="Générer cette partie et l'héberger sur le bot")
    @app_commands.describe(
        release="Quand les items restants d'un joueur sont envoyés aux autres (!release)",
        collect="Quand un joueur récupère ses items restants chez les autres (!collect)",
        remaining="Quand !remaining liste les items restants",
        cout_hint="Coût d'un hint, en % des checks du joueur",
        mot_de_passe="Mot de passe pour rejoindre la partie",
    )
    @app_commands.choices(release=MODE_CHOICES, collect=MODE_CHOICES, remaining=REMAINING_CHOICES)
    async def host(
        self,
        interaction: Interaction,
        release: str = DEFAULTS.release_mode,
        collect: str = DEFAULTS.collect_mode,
        remaining: str = DEFAULTS.remaining_mode,
        cout_hint: app_commands.Range[int, 0, 100] = DEFAULTS.hint_cost,
        mot_de_passe: app_commands.Range[str, 1, 64] | None = None,
    ) -> None:
        game = self._game_here(interaction, "la lancer")
        guild = interaction.guild
        assert guild is not None
        self.bot.hosting.check_available()
        await interaction.response.defer(ephemeral=True, thinking=True)
        options = ServerOptions(release, collect, remaining, cout_hint)
        runtime, warnings = await host_game(self.bot, guild, game, options, mot_de_passe)
        players = runtime.tracker.state.players
        text = (
            f"{E.host} Partie générée et hébergée · {len(players)} joueurs\n"
            f"Pour jouer : `{runtime.play_address}`\n"
            f"-# Les bloqués et les checks en logique sont déjà actifs. Le serveur s'arrête avec le suivi "
            f"({self.bot.command_mention('track stop')})."
        )
        if warnings:
            text += "\n⚠️ Options inconnues des apworlds, ignorées :\n" + "\n".join(f"- {w}" for w in warnings[:10])
        await interaction.followup.send(view=notice(text, Tone.WARNING if warnings else Tone.SUCCESS), ephemeral=True)

    @app_commands.command(name="yamls", description="Corriger les yamls de cette partie, avant ou après le lancement")
    async def yamls(self, interaction: Interaction) -> None:
        if self.bot.games.by_thread(interaction.channel_id) is not None:
            game = self._game_here(interaction, "corriger les yamls")
        else:
            game = await self._started_here(interaction)
        view = await GameYamlsView.create(self.bot.games, game)
        await interaction.response.send_message(view=view, ephemeral=True)

    async def _started_here(self, interaction: Interaction) -> GameRecord:
        game = await self.bot.games.repo.started_in(interaction.channel_id or 0)
        if game is None:
            raise UserError("Utilise cette commande dans le post d'une partie.")
        runtime = self.bot.rooms.by_thread(interaction.channel_id)
        user, permissions = interaction.user.id, interaction.permissions
        if user != game.created_by and not (runtime and runtime.can_manage(user, permissions)):
            if not permissions.manage_threads:
                raise UserError(
                    "Seule la personne qui a annoncé la partie, celle qui a lancé le suivi, ou un modérateur peut "
                    "corriger les yamls."
                )
        return game

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
        if not (files := [a for a in message.attachments if is_signup_file(a.filename)]):
            return
        game = self.bot.games.by_thread(message.channel.id)
        admin = game is None and self._manages_room(message)
        if admin:
            game = await self.bot.games.repo.started_in(message.channel.id)
        if game is not None:
            await self._files(game, message, files, admin=admin)

    def _manages_room(self, message: discord.Message) -> bool:
        runtime = self.bot.rooms.by_thread(message.channel.id)
        permissions = message.channel.permissions_for(message.author)  # type: ignore[arg-type, union-attr]
        return runtime is not None and runtime.can_manage(message.author.id, permissions)

    async def _files(
        self, game: GameRecord, message: discord.Message, attachments: list[discord.Attachment], *, admin: bool
    ) -> None:
        games = self.bot.games
        uploads = await games.upload(game, message.author.id, attachments, admin=admin)
        report = await UploadReport.create(games, game, message.author.id, uploads)
        try:
            if all(u.clean for u in uploads) and not report.needs_answer:
                await message.add_reaction("✅")
            else:
                await message.reply(view=report, mention_author=False)
        except discord.HTTPException:
            log.warning("Could not answer an upload in game %s", game.id, exc_info=True)


async def setup(bot: "ArchipelaBot") -> None:
    await bot.add_cog(GameCog(bot))
