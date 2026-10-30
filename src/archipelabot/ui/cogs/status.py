import asyncio
from typing import TYPE_CHECKING

import discord
from discord import app_commands
from discord.ext import commands

from ...core.logic import LogicError, blame
from ...errors import UserError
from ..autocomplete import slot_autocomplete
from ..components import Tone, notice
from ..emojis import E
from ..manage import can_manage
from ..panel_buttons import hints_for
from ..render.hints import player_hints_view
from ..render.status import player_status_view
from ..render.text import md
from ..types import Interaction

if TYPE_CHECKING:
    from ...bot import ArchipelaBot

INVENTORY_WAIT = 2.0  # Discord wants an answer within 3 s


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

        async def status(slot: int) -> discord.ui.LayoutView:
            try:
                inventory = await asyncio.wait_for(runtime.logic.inventory(slot), INVENTORY_WAIT)
            except (LogicError, TimeoutError):
                inventory = None
            logic, names = runtime.logic.results, runtime.tracker.location_name
            return player_status_view(slot, state, progress, runtime.claims, logic, names, inventory)

        await interaction.response.send_message(view=await status(slots[0]), ephemeral=True)
        for slot in slots[1:]:
            await interaction.followup.send(view=await status(slot), ephemeral=True)

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

    @app_commands.command(name="blame", description="Désigner qui bloque le plus de monde dans cette room")
    @app_commands.guild_only()
    async def blame(self, interaction: Interaction) -> None:
        runtime = self.bot.rooms.by_thread(interaction.channel_id)
        if runtime is None:
            raise UserError("Utilise cette commande dans le post d'une room suivie.")
        if not runtime.logic.active:
            raise UserError(
                f"Il faut d'abord envoyer le spoiler log avec {E.settings} **Gérer** puis **Logique** sur le panneau."
            )
        state, results = runtime.tracker.state, runtime.logic.results
        found = blame(results)
        if found is None:
            text = (
                "Personne ne bloque personne : les bloqués attendent des items que personne ne peut encore atteindre."
                if any(r.blocked for r in results.values())
                else "Personne n'est bloqué en ce moment."
            )
            await interaction.response.send_message(view=notice(f"{E.reachable} {text}"), ephemeral=True)
            return
        culprit, victims = found
        user = runtime.claims.get(culprit)
        who = f"<@{user}>" if user is not None else f"**{md(state.name(culprit))}**"
        names = ", ".join(md(state.name(slot)) for slot in sorted(victims, key=state.name))
        wait = "attend" if len(victims) == 1 else "attendent"
        text = f"{E.blocked} {who}, c'est TOI qui bloque tout le monde !!!\n-# {names} {wait} tes items."
        allowed = discord.AllowedMentions(users=[discord.Object(user)] if user is not None else False)
        await interaction.response.send_message(view=notice(text, Tone.ERROR), allowed_mentions=allowed)

    @app_commands.command(name="recap", description="Le récap de cette room (podium, chiffres, graphique)")
    @app_commands.guild_only()
    async def recap(self, interaction: Interaction) -> None:
        rooms = self.bot.rooms
        runtime = rooms.by_thread(interaction.channel_id)
        record = runtime.record if runtime else await rooms.repo.by_thread(interaction.channel_id or 0)
        if record is None:
            raise UserError("Utilise cette commande dans le post d'une room.")
        await interaction.response.defer(ephemeral=True, thinking=True)
        if can_manage(record, interaction.user.id, interaction.permissions):
            if runtime:
                await rooms.publish_recap(runtime, announce=False)
            else:
                await rooms.publish_stopped_recap(record)
            await interaction.followup.send(view=notice("Récap publié dans le post.", Tone.SUCCESS), ephemeral=True)
            return
        if runtime:
            state, recap = runtime.tracker.state, await runtime.build_recap()
        else:
            state, recap = await rooms.stopped_recap(record)
        view, files = await rooms.recap_message(state, recap)
        await interaction.followup.send(view=view, files=files, ephemeral=True)


async def setup(bot: "ArchipelaBot") -> None:
    await bot.add_cog(StatusCog(bot))
