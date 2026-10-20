import logging
from typing import TYPE_CHECKING

import discord
from discord import app_commands
from discord.ext import commands

from ...core.logic import MAX_SPOILER, LogicError, read_spoiler
from ...errors import UserError
from ..apworlds import GameApworldsView, GameYamlsView
from ..components import Tone, notice
from ..emojis import E
from ..forum import TAG_SPECS, RoomTag
from ..games import check_can_create
from ..launch import launch_room
from ..render.text import md, plural
from ..rooms import RoomRuntime
from ..types import Interaction
from ..views import ConfirmView

if TYPE_CHECKING:
    from ...bot import ArchipelaBot

log = logging.getLogger(__name__)


@app_commands.guild_only()
class TrackCog(commands.GroupCog, name="track", group_name="track", group_description="Suivre une room Archipelago"):
    def __init__(self, bot: "ArchipelaBot") -> None:
        self.bot = bot

    # --- /track start -----------------------------------------------------------------------------

    @app_commands.command(name="start", description="Suivre une room dans le forum des rooms")
    @app_commands.describe(
        lien="Lien de la room (archipelago.gg/room/…)",
        slot="Nom d'un joueur, le bot l'utilisera pour se connecter en spectateur",
        mot_de_passe="Mot de passe du serveur, s'il y en a un",
        nom="Nom du post dans le forum",
    )
    async def start(
        self,
        interaction: Interaction,
        lien: app_commands.Range[str, 3, 200],
        slot: app_commands.Range[str, 1, 16] | None = None,
        mot_de_passe: str | None = None,
        nom: app_commands.Range[str, 1, 90] | None = None,
    ) -> None:
        guild = interaction.guild
        assert guild is not None
        game = self.bot.games.by_thread(interaction.channel_id)
        if game is None:
            check_can_create(await self.bot.guild_configs.get(guild.id), interaction.permissions)
        await interaction.response.defer(ephemeral=True, thinking=True)
        runtime, recognised = await launch_room(
            self.bot, guild, lien, user_id=interaction.user.id, slot=slot, password=mot_de_passe, name=nom, game=game
        )

        players = runtime.tracker.state.players
        games = {s.game for s in players}
        text = f"Room suivie · {len(players)} joueurs · {len(games)} jeux\n→ <#{runtime.record.thread_id}>"
        if recognised:
            text += f"\n-# {plural(recognised, 'joueur reconnu', 'joueurs reconnus')} d'après les parties précédentes."
        await interaction.followup.send(view=notice(text, Tone.SUCCESS), ephemeral=True)

    # --- commands inside a room's post --------------------------------------------------------

    def _room_here(self, interaction: Interaction) -> RoomRuntime:
        runtime = self.bot.rooms.by_thread(interaction.channel_id)
        if runtime is None:
            raise UserError("Utilise cette commande dans le post d'une room suivie.")
        return runtime

    def _check_manager(self, interaction: Interaction, runtime: RoomRuntime) -> None:
        if not runtime.can_manage(interaction.user.id, interaction.permissions):
            raise UserError("Seule la personne qui a lancé le suivi (ou un modérateur) peut faire ça.")

    @app_commands.command(name="stop", description="Arrêter le suivi de cette room")
    async def stop(self, interaction: Interaction) -> None:
        runtime = self._room_here(interaction)
        self._check_manager(interaction, runtime)

        def stopped_already() -> bool:
            return self.bot.rooms.get(runtime.record.id) is not runtime

        async def stop_with_recap() -> str:
            if stopped_already():
                return f"{E.stopped} Ce suivi est déjà arrêté."
            try:
                await self.bot.rooms.publish_recap(runtime, announce=True)
                text = f"{E.stopped} Suivi arrêté, récap publié."
            except Exception:
                log.exception("Could not publish the recap of room %s", runtime.record.id)
                text = f"{E.stopped} Suivi arrêté, mais le récap n'a pas pu être publié."
            await self.bot.rooms.stop(runtime, "finished")
            return text

        async def stop() -> str:
            if stopped_already():
                return f"{E.stopped} Ce suivi est déjà arrêté."
            await self.bot.rooms.stop(runtime, "stopped")
            return f"{E.stopped} Suivi arrêté. Le post reste consultable."

        async def back_to_signup() -> str:
            if game is None or not await self.bot.rooms.discard(runtime):
                return f"{E.stopped} Ce suivi est déjà arrêté."
            await self.bot.games.reopen(game)
            try:
                text = f"{E.signup} Suivi annulé : retour aux inscriptions. Relance {start_command} avec le bon lien."
                await runtime.sink.send_view(notice(text))
            except discord.HTTPException:
                log.warning("Could not announce the return to sign-ups of game %s", game.id, exc_info=True)
            return f"{E.signup} Retour aux inscriptions : les yamls sont toujours là."

        game = await self.bot.games.repo.started_in(runtime.record.thread_id or 0)
        start_command = self.bot.command_mention("track start")
        actions = [
            ("Arrêter et publier le récap", discord.ButtonStyle.primary, stop_with_recap),
            ("Arrêter sans récap", discord.ButtonStyle.danger, stop),
        ]
        question = (
            f"Arrêter le suivi de **{runtime.record.name}** ?\n-# Le post restera consultable, avec le tag "
            f"{' '.join(TAG_SPECS[RoomTag.FINISHED][::-1])}."
        )
        if game is not None:
            actions.append(("Revenir aux inscriptions", discord.ButtonStyle.secondary, back_to_signup))
            question += (
                "\n-# **Revenir aux inscriptions** : mauvais lien ou partie à regénérer. Le suivi est oublié (pas de "
                f"récap), les yamls restent, et tu pourras relancer {start_command}."
            )
        view = ConfirmView(question, actions)
        await interaction.response.send_message(view=view, ephemeral=True)

    @app_commands.command(name="reconnect", description="Relancer tout de suite la connexion de cette room")
    @app_commands.describe(mot_de_passe="Nouveau mot de passe du serveur, s'il a changé")
    async def reconnect(self, interaction: Interaction, mot_de_passe: str | None = None) -> None:
        runtime = self._room_here(interaction)
        self._check_manager(interaction, runtime)
        await interaction.response.defer(ephemeral=True, thinking=True)
        await self.bot.rooms.reconnect(runtime, mot_de_passe)
        text = f"{E.reconnecting} Reconnexion lancée : le panneau de la room affiche le résultat."
        await interaction.followup.send(view=notice(text, Tone.SUCCESS), ephemeral=True)

    @app_commands.command(name="logique", description="Savoir qui est bloqué et combien de checks chacun peut faire")
    @app_commands.describe(spoiler="Le spoiler log de la génération (AP_…_Spoiler.txt) ou le zip AP_….zip")
    async def logic(self, interaction: Interaction, spoiler: discord.Attachment) -> None:
        runtime = self._room_here(interaction)
        self._check_manager(interaction, runtime)
        if spoiler.size > MAX_SPOILER:
            raise UserError(f"Fichier trop gros (plus de {MAX_SPOILER // 1024 // 1024} Mo).")
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            from_room = await self.bot.rooms.enable_logic(runtime, read_spoiler(spoiler.filename, await spoiler.read()))
        except LogicError as e:
            raise UserError(str(e)) from e
        except discord.HTTPException as e:
            raise UserError("Impossible de télécharger le fichier.") from e
        results = runtime.logic.results
        blocked = [md(runtime.tracker.state.name(slot)) for slot, r in results.items() if r.blocked]
        text = (
            f"{E.reachable} Logique activée : le panneau montre maintenant qui est bloqué et combien de checks chacun "
            "peut faire, d'après la logique du jeu."
        )
        if from_room:
            text += (
                "\n⚠️ Un des worlds ne se génère jamais deux fois pareil : les items viennent de la room, les règles de "
                "la regénération. Si ce world tire aussi ses règles au hasard, sa logique peut être fausse."
            )
        if blocked:
            text += f"\n-# Bloqués en ce moment : {', '.join(blocked)}."
        tone = Tone.WARNING if from_room else Tone.SUCCESS
        await interaction.followup.send(view=notice(text, tone), ephemeral=True)

    @app_commands.command(name="apworlds", description="Choisir les apworlds qui ont servi à générer cette partie")
    async def apworlds(self, interaction: Interaction) -> None:
        runtime = self._room_here(interaction)
        self._check_manager(interaction, runtime)
        game = await self.bot.games.repo.started_in(runtime.record.thread_id or 0)
        if game is None:
            raise UserError("Seulement pour une room lancée depuis le post d'une partie (`/partie nouvelle`).")
        view = await GameApworldsView.create(self.bot.games, game)
        await interaction.response.send_message(view=view, ephemeral=True)

    @app_commands.command(name="yamls", description="Corriger les yamls qui ont servi à générer cette partie")
    async def yamls(self, interaction: Interaction) -> None:
        runtime = self._room_here(interaction)
        self._check_manager(interaction, runtime)
        game = await self.bot.games.repo.started_in(runtime.record.thread_id or 0)
        if game is None:
            raise UserError("Seulement pour une room lancée depuis le post d'une partie (`/partie nouvelle`).")
        view = await GameYamlsView.create(self.bot.games, game)
        await interaction.response.send_message(view=view, ephemeral=True)

    @app_commands.command(name="reglages", description="Choisir ce qui est affiché dans le fil de cette room")
    async def settings(self, interaction: Interaction) -> None:
        runtime = self._room_here(interaction)
        self._check_manager(interaction, runtime)
        await interaction.response.send_message(view=runtime.settings_view(), ephemeral=True)


async def setup(bot: "ArchipelaBot") -> None:
    await bot.add_cog(TrackCog(bot))
