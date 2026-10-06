import logging
from typing import TYPE_CHECKING

import discord
from discord import app_commands
from discord.ext import commands

from ...errors import UserError
from ..components import Tone, notice
from ..emojis import E
from ..forum import TAG_SPECS, RoomTag
from ..launch import launch_room
from ..render.text import plural
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
        await interaction.response.defer(ephemeral=True, thinking=True)
        runtime, recognised = await launch_room(
            self.bot, guild, lien, user_id=interaction.user.id, slot=slot, password=mot_de_passe, name=nom,
            game=self.bot.games.by_thread(interaction.channel_id),
        )  # fmt: skip

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

        view = ConfirmView(
            f"Arrêter le suivi de **{runtime.record.name}** ?\n-# Le post restera consultable, avec le tag "
            f"{' '.join(TAG_SPECS[RoomTag.FINISHED][::-1])}.",
            [
                ("Arrêter et publier le récap", discord.ButtonStyle.primary, stop_with_recap),
                ("Arrêter sans récap", discord.ButtonStyle.danger, stop),
            ],
        )
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

    @app_commands.command(name="reglages", description="Choisir ce qui est affiché dans le fil de cette room")
    async def settings(self, interaction: Interaction) -> None:
        runtime = self._room_here(interaction)
        self._check_manager(interaction, runtime)
        await interaction.response.send_message(view=runtime.settings_view(), ephemeral=True)


async def setup(bot: "ArchipelaBot") -> None:
    await bot.add_cog(TrackCog(bot))
