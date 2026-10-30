import logging
from typing import TYPE_CHECKING

from discord import app_commands
from discord.ext import commands

from ..components import Tone, notice
from ..games import check_can_create
from ..launch import launch_room
from ..render.text import plural
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


async def setup(bot: "ArchipelaBot") -> None:
    await bot.add_cog(TrackCog(bot))
