from typing import TYPE_CHECKING

from discord import app_commands
from discord.ext import commands

from ...errors import UserError
from ..autocomplete import slot_autocomplete
from ..components import Tone, notice
from ..panel_buttons import hints_for
from ..render.hints import player_hints_view
from ..render.status import player_status_view
from ..types import Interaction

if TYPE_CHECKING:
    from ...bot import ArchipelaBot


class StatusCog(commands.Cog, name="status"):
    def __init__(self, bot: "ArchipelaBot") -> None:
        self.bot = bot

    @app_commands.command(name="status", description="Progression détaillée d'un joueur de cette room")
    @app_commands.describe(joueur="Par défaut : ton slot, sinon le panneau complet")
    @app_commands.autocomplete(joueur=slot_autocomplete)
    @app_commands.guild_only()
    async def status(self, interaction: Interaction, joueur: str | None = None) -> None:
        runtime = self.bot.rooms.by_thread(interaction.channel_id)
        if runtime is None:
            raise UserError("Utilise cette commande dans le post d'une room suivie.")
        state, progress = runtime.tracker.state, runtime.progress

        if joueur is not None:
            slots = [runtime.slot_named(joueur)]
        else:
            slots = [slot for slot, user in runtime.claims.items() if user == interaction.user.id]
        if not slots:
            await interaction.response.send_message(view=runtime.render_panel(buttons=False), ephemeral=True)
            return

        await interaction.response.send_message(
            view=player_status_view(slots[0], state, progress, runtime.claims), ephemeral=True
        )
        for slot in slots[1:]:
            await interaction.followup.send(
                view=player_status_view(slot, state, progress, runtime.claims), ephemeral=True
            )

    @app_commands.command(name="hints", description="Les hints de cette room (visibles seulement par toi)")
    @app_commands.describe(joueur="Par défaut : tes hints, ou tous les hints si tu n'as pas de slot")
    @app_commands.autocomplete(joueur=slot_autocomplete)
    @app_commands.guild_only()
    async def hints(self, interaction: Interaction, joueur: str | None = None) -> None:
        runtime = self.bot.rooms.by_thread(interaction.channel_id)
        if runtime is None:
            raise UserError("Utilise cette commande dans le post d'une room suivie.")
        if joueur is None:
            view = hints_for(runtime, interaction.user.id)
        else:
            slot = runtime.slot_named(joueur)
            view = player_hints_view([slot], runtime.tracker.state, you=runtime.claims.get(slot) == interaction.user.id)
        await interaction.response.send_message(view=view, ephemeral=True)

    @app_commands.command(name="recap", description="Publier le récap de cette room dans son post")
    @app_commands.guild_only()
    async def recap(self, interaction: Interaction) -> None:
        runtime = self.bot.rooms.by_thread(interaction.channel_id)
        if runtime is None:
            raise UserError("Utilise cette commande dans le post d'une room suivie.")
        await interaction.response.defer(ephemeral=True, thinking=True)
        await self.bot.rooms.publish_recap(runtime)
        await interaction.followup.send(view=notice("Récap publié.", Tone.SUCCESS), ephemeral=True)


async def setup(bot: "ArchipelaBot") -> None:
    await bot.add_cog(StatusCog(bot))
