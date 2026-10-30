"""Small Components V2 building blocks shared by every cog."""

import logging
from enum import Enum

import discord
from discord import ui

from ..errors import UserError

log = logging.getLogger(__name__)


class Tone(Enum):
    INFO = discord.Colour.blurple()
    SUCCESS = discord.Colour.green()
    WARNING = discord.Colour.orange()
    ERROR = discord.Colour.red()


def notice(text: str, tone: Tone = Tone.INFO) -> ui.LayoutView:
    view = ui.LayoutView()
    view.add_item(ui.Container(ui.TextDisplay(text), accent_colour=tone.value))
    return view


async def send_error(interaction: discord.Interaction, error: Exception) -> None:
    if isinstance(error, UserError):
        text = str(error)
    else:
        log.error("Unhandled error in a component", exc_info=error)
        text = "Une erreur inattendue est survenue. Elle a été enregistrée dans les logs."
    view = notice(text, Tone.ERROR)
    if interaction.response.is_done():
        await interaction.followup.send(view=view, ephemeral=True)
    else:
        await interaction.response.send_message(view=view, ephemeral=True)


class View(ui.LayoutView):
    """Shows a UserError raised by a callback to whoever clicked."""

    async def on_error(self, interaction: discord.Interaction, error: Exception, item: ui.Item) -> None:
        await send_error(interaction, error)


class Modal(ui.Modal):
    async def on_error(self, interaction: discord.Interaction, error: Exception) -> None:
        await send_error(interaction, error)
