from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

from discord import app_commands
from discord.ext import commands

from ...core.room import RoomState, SlotInfo
from ...errors import UserError
from ..autocomplete import slot_autocomplete
from ..history import HistoryView, history_entries
from ..rooms import slot_named
from ..types import Interaction

if TYPE_CHECKING:
    from ...bot import ArchipelaBot
    from ...storage.rooms import RoomRecord


class HistoryCog(commands.Cog, name="history"):
    def __init__(self, bot: "ArchipelaBot") -> None:
        self.bot = bot

    async def _room_here(self, channel_id: int | None) -> tuple["RoomRecord", RoomState]:
        """The room of this post or of its full log, still tracked or not."""
        rooms = self.bot.rooms
        if runtime := rooms.by_thread(channel_id) or rooms.by_log_thread(channel_id):
            return runtime.record, runtime.tracker.state
        record = channel_id and (await rooms.repo.by_thread(channel_id) or await rooms.repo.by_log_thread(channel_id))
        if not record:
            raise UserError("Utilise cette commande dans le post d'une room, ou dans son suivi complet.")
        state = RoomState(record.address)
        saved = await rooms.history.slots(record.id)  # type: ignore[arg-type]
        state.slots = {s.slot: SlotInfo(s.slot, s.name, s.game, s.is_group, s.alias) for s in saved}
        return record, state

    @app_commands.command(name="historique", description="Tout ce qui s'est passé dans cette room, ou pour un joueur")
    @app_commands.describe(joueur="Seulement ce qui concerne ce joueur")
    @app_commands.autocomplete(joueur=slot_autocomplete)
    @app_commands.guild_only()
    async def history(self, interaction: Interaction, joueur: str | None = None) -> None:
        record, state = await self._room_here(interaction.channel_id)
        slot = slot_named(state, joueur) if joueur is not None else None
        history = self.bot.rooms.history
        assert record.id is not None
        entries = history_entries(await history.items(record.id), await history.events(record.id), state.name, slot)
        title = record.name if slot is None else f"{record.name} · {state.name(slot)}"
        view = HistoryView(title, entries, ZoneInfo(self.bot.settings.timezone))
        await interaction.response.send_message(view=view, ephemeral=True)


async def setup(bot: "ArchipelaBot") -> None:
    await bot.add_cog(HistoryCog(bot))
