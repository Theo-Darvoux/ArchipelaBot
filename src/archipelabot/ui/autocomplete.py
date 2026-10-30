from discord import app_commands

from ..errors import UserError
from .render.text import truncate
from .types import Interaction


async def slot_autocomplete(interaction: Interaction, current: str) -> list[app_commands.Choice[str]]:
    """Players of the room whose post the command is used in."""
    rooms = interaction.client.rooms
    runtime = rooms.by_thread(interaction.channel_id) or rooms.by_log_thread(interaction.channel_id)
    if runtime is None:
        return []
    choices = []
    for info in runtime.tracker.state.players:
        if current.casefold() not in info.display.casefold():
            continue
        owner = runtime.claims.get(info.slot)
        note = ""
        if owner == interaction.user.id:
            note = " · c'est toi"
        elif owner is not None:
            note = " · déjà pris"
        choices.append(app_commands.Choice(name=truncate(f"{info.display} · {info.game}{note}", 100), value=info.name))
    return choices[:25]


async def item_autocomplete(interaction: Interaction, current: str) -> list[app_commands.Choice[str]]:
    """Items of the game of the chosen player, or of the user's own slots."""
    runtime = interaction.client.rooms.by_thread(interaction.channel_id)
    if runtime is None:
        return []
    if joueur := interaction.namespace.joueur:
        try:
            slots = [runtime.slot_named(joueur)]
        except UserError:
            return []
    else:
        slots = [slot for slot, user in runtime.claims.items() if user == interaction.user.id]
    wanted = current.casefold()
    names = sorted({n for slot in slots for n in runtime.item_names(slot) if wanted in n.casefold()})
    names.sort(key=lambda n: not n.casefold().startswith(wanted))
    return [app_commands.Choice(name=truncate(n, 100), value=n[:100]) for n in names[:25]]
