import logging
from typing import TYPE_CHECKING

import discord
from discord import app_commands
from discord.ext import commands

from ...core.logic import MAX_SPOILER, LogicError, read_spoiler
from ...errors import UserError
from ..components import Tone, notice
from ..emojis import E
from ..games import check_can_create
from ..launch import launch_room
from ..render.text import md, plural
from ..rooms import RoomRuntime
from ..types import Interaction

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


async def setup(bot: "ArchipelaBot") -> None:
    await bot.add_cog(TrackCog(bot))
