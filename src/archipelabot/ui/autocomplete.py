from discord import app_commands

from .render.text import truncate
from .types import Interaction


async def slot_autocomplete(interaction: Interaction, current: str) -> list[app_commands.Choice[str]]:
    """Players of the room whose post the command is used in."""
    runtime = interaction.client.rooms.by_thread(interaction.channel_id)
    if runtime is None:
        return []
    choices = []
    for info in runtime.tracker.state.players:
        if current.casefold() not in info.name.casefold():
            continue
        owner = runtime.claims.get(info.slot)
        note = ""
        if owner == interaction.user.id:
            note = " · c'est toi"
        elif owner is not None:
            note = " · déjà pris"
        choices.append(app_commands.Choice(name=truncate(f"{info.name} · {info.game}{note}", 100), value=info.name))
    return choices[:25]
