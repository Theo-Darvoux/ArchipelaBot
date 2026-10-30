from typing import TYPE_CHECKING

import discord
from discord import app_commands
from discord.ext import commands

from ...errors import UserError
from ...storage.pings import ItemPing
from ..autocomplete import item_autocomplete, slot_autocomplete
from ..components import Tone, notice
from ..emojis import E
from ..panel_buttons import claimed_message
from ..pings import PingsView
from ..render.text import md
from ..rooms import RoomRuntime
from ..types import Interaction

if TYPE_CHECKING:
    from ...bot import ArchipelaBot


class ClaimCog(commands.Cog, name="claim"):
    def __init__(self, bot: "ArchipelaBot") -> None:
        self.bot = bot

    def _room_here(self, interaction: Interaction) -> RoomRuntime:
        runtime = self.bot.rooms.by_thread(interaction.channel_id)
        if runtime is None:
            raise UserError("Utilise cette commande dans une room suivie.")
        return runtime

    @app_commands.command(name="claim", description="Quel utilisateur tu es dans la room")
    @app_commands.describe(slot="Ton slot", pour="(Modérateurs) attribuer le slot à quelqu'un d'autre")
    @app_commands.autocomplete(slot=slot_autocomplete)
    @app_commands.guild_only()
    async def claim(self, interaction: Interaction, slot: str, pour: discord.Member | None = None) -> None:
        runtime = self._room_here(interaction)
        moderator = interaction.permissions.manage_threads
        if pour is not None and pour.id != interaction.user.id and not moderator:
            raise UserError("Seuls les modérateurs peuvent attribuer un slot à quelqu'un d'autre.")
        user = pour or interaction.user
        number = runtime.slot_named(slot)
        await runtime.claim(number, user.id, force=moderator)

        text = (
            claimed_message(runtime, number, user.id)
            if user == interaction.user
            else (f"**{md(runtime.tracker.state.name(number))}** est maintenant attribué à {user.mention}.")
        )
        await interaction.response.send_message(view=notice(text, Tone.SUCCESS), ephemeral=True)

    @app_commands.command(name="unclaim", description="Ne plus être associé à un slot de cette room")
    @app_commands.describe(slot="Le slot à libérer")
    @app_commands.autocomplete(slot=slot_autocomplete)
    @app_commands.guild_only()
    async def unclaim(self, interaction: Interaction, slot: str | None = None) -> None:
        runtime = self._room_here(interaction)
        mine = [s for s, user in runtime.claims.items() if user == interaction.user.id]
        if slot is not None:
            number = runtime.slot_named(slot)
            owner = runtime.claims.get(number)
            if owner is None:
                raise UserError("Ce slot n'est attribué à personne.")
            if owner != interaction.user.id and not interaction.permissions.manage_threads:
                raise UserError("Ce slot n'est pas le tien.")
        elif len(mine) == 1:
            number = mine[0]
        elif not mine:
            raise UserError("Tu n'as aucun slot dans cette room.")
        else:
            raise UserError("Tu as plusieurs slots : précise lequel libérer.")

        await runtime.unclaim(number)
        name = md(runtime.tracker.state.name(number))
        await interaction.response.send_message(view=notice(f"**{name}** est libéré.", Tone.SUCCESS), ephemeral=True)

    @app_commands.command(name="ping", description="Être prévenu quand ton slot reçoit un item précis")
    @app_commands.describe(
        item="L'item attendu · vide : voir et retirer tes pings",
        joueur="Le slot qui doit le recevoir, si ce n'est pas le tien",
    )
    @app_commands.autocomplete(item=item_autocomplete, joueur=slot_autocomplete)
    @app_commands.guild_only()
    async def ping(self, interaction: Interaction, item: str | None = None, joueur: str | None = None) -> None:
        runtime = self._room_here(interaction)
        user = interaction.user.id
        if item is None:
            await interaction.response.send_message(view=PingsView(runtime, user), ephemeral=True)
            return
        if joueur is not None:
            slots = [runtime.slot_named(joueur)]
        else:
            slots = sorted(slot for slot, owner in runtime.claims.items() if owner == user)
        if not slots:
            raise UserError("Indique avec `joueur` le slot qui doit recevoir l'item, ou clique d'abord sur **Je joue**")
        state = runtime.tracker.state
        added = []
        for slot in slots:
            if name := runtime.item_named(slot, item):
                await runtime.watch(ItemPing(user, slot, name))
                added.append(f"**{md(name)}** pour {md(state.name(slot))}")
        if not added:
            games = ", ".join(sorted({f"*{md(state.game(slot))}*" for slot in slots}))
            raise UserError(f"Aucun item **{md(item)}** dans {games}.")
        note = f"{E.ping} Ajouté : {', '.join(added)}."
        await interaction.response.send_message(view=PingsView(runtime, user, note), ephemeral=True)


async def setup(bot: "ArchipelaBot") -> None:
    await bot.add_cog(ClaimCog(bot))
