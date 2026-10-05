from typing import TYPE_CHECKING

import discord

if TYPE_CHECKING:
    from ..bot import ArchipelaBot

type Interaction = discord.Interaction[ArchipelaBot]
