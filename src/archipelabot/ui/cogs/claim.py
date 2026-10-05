from typing import TYPE_CHECKING

import discord
from discord import app_commands
from discord.ext import commands

from ...errors import UserError
from ...storage.claims import NotifMode
from ..autocomplete import slot_autocomplete
from ..components import Tone, notice
from ..emojis import E
from ..panel_buttons import claimed_message
from ..render.text import md
from ..rooms import RoomRuntime
from ..types import Interaction

if TYPE_CHECKING:
    from ...bot import ArchipelaBot

NOTIF_LABELS = {
    NotifMode.THREAD: "Mention",
    NotifMode.DM: "Message privé",
    NotifMode.OFF: "Rien",
}
NOTIF_ICONS = {NotifMode.THREAD: "notif_thread", NotifMode.DM: "notif_dm", NotifMode.OFF: "notif_off"}


def notif_label(mode: NotifMode) -> str:
    return f"{E.get(NOTIF_ICONS[mode])} **{NOTIF_LABELS[mode]}**"


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

    @app_commands.command(name="notifs", description="Comment tu veux être prévenu quand tu reçois un item")
    @app_commands.describe(mode="Laisser vide pour voir le réglage actuel")
    @app_commands.choices(
        mode=[app_commands.Choice(name=label, value=mode.value) for mode, label in NOTIF_LABELS.items()]
    )
    async def notifs(self, interaction: Interaction, mode: app_commands.Choice[str] | None = None) -> None:
        user = interaction.user.id
        if mode is None:
            current = self.bot.notif_prefs.mode(user)
            text = f"Quand tu reçois un item de progression : {notif_label(current)}."
            text += "\n-# `/notifs mode:` pour changer."
            await interaction.response.send_message(view=notice(text), ephemeral=True)
            return
        await self.bot.notif_prefs.set(user, NotifMode(mode.value))
        text = f"Quand tu reçois un item de progression : {notif_label(NotifMode(mode.value))}."
        await interaction.response.send_message(view=notice(text, Tone.SUCCESS), ephemeral=True)


async def setup(bot: "ArchipelaBot") -> None:
    await bot.add_cog(ClaimCog(bot))
