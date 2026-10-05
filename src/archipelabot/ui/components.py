"""Small Components V2 building blocks shared by every cog."""

from enum import Enum

import discord
from discord import ui


class Tone(Enum):
    INFO = discord.Colour.blurple()
    SUCCESS = discord.Colour.green()
    WARNING = discord.Colour.orange()
    ERROR = discord.Colour.red()


def notice(text: str, tone: Tone = Tone.INFO) -> ui.LayoutView:
    view = ui.LayoutView()
    view.add_item(ui.Container(ui.TextDisplay(text), accent_colour=tone.value))
    return view
