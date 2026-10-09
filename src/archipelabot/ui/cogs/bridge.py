import logging
from typing import TYPE_CHECKING

import discord
from discord.ext import commands

from ...ap.client import APConnectionError, APError
from ..emojis import E
from ..services.bridge import chat_text, is_server_command, signed

if TYPE_CHECKING:
    from ...bot import ArchipelaBot

log = logging.getLogger(__name__)


class BridgeCog(commands.Cog, name="bridge"):
    """Relays messages written in a room's post to the game's chat."""

    def __init__(self, bot: "ArchipelaBot") -> None:
        self.bot = bot

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if message.author.bot or message.type not in (discord.MessageType.default, discord.MessageType.reply):
            return
        runtime = self.bot.rooms.by_thread(message.channel.id)
        if runtime is None or not runtime.record.settings.chat_bridge:
            return

        if is_server_command(message.content):
            await self._react(message, "avoid")
            return

        text = chat_text(message.clean_content, [a.url for a in message.attachments])
        if text is None:
            return
        claimed = [slot for slot, user in runtime.claims.items() if user == message.author.id]
        try:
            if claimed:
                try:
                    await runtime.chat.say(claimed[0], text)
                except APError:
                    log.warning("Could not chat as slot %s, signing instead", claimed[0], exc_info=True)
                    await runtime.tracker.say(signed(runtime.tracker.state.name(claimed[0]), text))
            else:
                await runtime.tracker.say(signed(message.author.display_name, text))
        except APConnectionError:
            log.debug("Room %s unreachable, message not relayed", runtime.record.id)

    async def _react(self, message: discord.Message, icon: str) -> None:
        try:
            await message.add_reaction(E.partial(icon))
        except discord.HTTPException:
            log.debug("Could not react to a bridged message", exc_info=True)


async def setup(bot: "ArchipelaBot") -> None:
    await bot.add_cog(BridgeCog(bot))
