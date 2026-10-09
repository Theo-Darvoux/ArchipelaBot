"""Chat bridge between a room's post and the game, against a real Archipelago server."""

import asyncio
from dataclasses import dataclass, field

import discord
import pytest

from archipelabot.ap import protocol as p
from archipelabot.ap.client import APRefused, ConnectOptions, open_session
from archipelabot.ui.services.bridge import chat_text, is_server_command, signed

from .ap_server import requires_ap_server
from .test_track_cog import discord_env, track  # noqa: F401


def test_chat_text():
    assert chat_text("salut") == "salut"
    assert chat_text("gg <:pog:123> <a:dance:456>") == "gg :pog: :dance:"
    assert chat_text("ligne 1\n\nligne 2") == "ligne 1 / ligne 2"
    assert chat_text("", ["https://cdn/x.png"]) == "https://cdn/x.png"
    assert chat_text("  ") is None
    assert len(chat_text("a" * 5000)) == 600
    # The server refuses messages that aren't printable: joiners are dropped, odd spaces become spaces.
    assert chat_text("bof 🤷\u200d♂️\tok\xa0?") == "bof 🤷♂️ ok ?"
    assert chat_text("\u200d") is None
    assert signed("Théo", "salut") == "[Discord] Théo: salut"


def test_server_commands_are_detected():
    assert is_server_command("!release") and is_server_command("  !collect")
    assert not is_server_command("hello !")


@dataclass
class FakeAuthor:
    id: int = 77
    display_name: str = "Théo"
    bot: bool = False


@dataclass
class FakeChannel:
    id: int


@dataclass
class FakeDiscordMessage:
    channel: FakeChannel
    content: str
    author: FakeAuthor = field(default_factory=FakeAuthor)
    attachments: list = field(default_factory=list)
    type: discord.MessageType = discord.MessageType.default
    reactions: list[str] = field(default_factory=list)

    @property
    def clean_content(self) -> str:
        return self.content

    async def add_reaction(self, emoji) -> None:
        self.reactions.append(emoji.name)


@pytest.fixture
async def room(bot, discord_env, ap_server):  # noqa: F811
    guild, forum = discord_env
    await track(bot, guild, lien=ap_server.address, slot="Alice")
    [thread] = forum.threads
    carol = await open_session(
        ap_server.address, ConnectOptions(slot="Carol", game="ChecksFinder", tags=(), uuid="carol")
    )
    yield bot.rooms.by_thread(thread.id), thread, carol
    await carol.close()


async def say_on_discord(bot, thread, content, **kwargs) -> FakeDiscordMessage:
    message = FakeDiscordMessage(FakeChannel(thread.id), content, **kwargs)
    await bot.get_cog("bridge").on_message(message)
    return message


async def next_chat(session, wait=5.0) -> p.PrintJSON:
    async with asyncio.timeout(wait):
        async for packet in session:
            if isinstance(packet, p.PrintJSON) and packet.type == "Chat":
                return packet
    raise AssertionError("connection closed")


@requires_ap_server
async def test_discord_messages_reach_the_game_and_are_not_echoed(bot, room):
    runtime, thread, carol = room
    message = await say_on_discord(bot, thread, "j'arrive")
    chat = await next_chat(carol)
    assert chat.message == "[Discord] Théo: j'arrive" and chat.slot == 1
    assert message.reactions == []

    # Once claimed, the message is sent as the player's own slot, through a chat-only connection.
    await runtime.claim(2, 77)
    message = await say_on_discord(bot, thread, "c'est Bob")
    chat = await next_chat(carol)
    assert (chat.slot, chat.message) == (2, "c'est Bob") and message.reactions == []
    assert runtime.tracker.state.relay_slots == {2}
    assert not runtime.tracker.state.is_online(2)  # the relay connection isn't Bob playing

    # Emoji sequences used to be refused by the server, without any sign of it on Discord.
    message = await say_on_discord(bot, thread, "❤️\u200d🔥 gg")
    chat = await next_chat(carol)
    assert (chat.slot, chat.message) == (2, "❤️🔥 gg") and message.reactions == []

    # The game echoes the bot's own messages: they must not come back to Discord.
    await carol.send(p.say_packet("salut Discord"))
    await feed_contains(runtime, thread, "**Carol** : salut Discord")
    feed = "\n".join(thread.texts())
    assert "j'arrive" not in feed and "c'est Bob" not in feed
    assert "Bob a rejoint" not in feed

    runtime.chat.idle_timeout = 0
    await runtime.chat.close_idle()
    assert runtime.tracker.state.relay_slots == set()


async def feed_contains(runtime, thread, text: str, wait=5.0) -> None:
    async with asyncio.timeout(wait):
        while True:
            await runtime.feed.flush()
            if any(text in posted for posted in thread.texts()):
                return
            await asyncio.sleep(0.1)


@requires_ap_server
async def test_commands_bots_and_disabled_bridge_are_ignored(bot, room):
    runtime, thread, carol = room
    blocked = await say_on_discord(bot, thread, "!release")
    assert blocked.reactions == ["⛔"]

    await say_on_discord(bot, thread, "je suis un bot", author=FakeAuthor(bot=True))
    runtime.record.settings.chat_bridge = False
    off = await say_on_discord(bot, thread, "pont coupé")
    assert off.reactions == []

    runtime.record.settings.chat_bridge = True
    await say_on_discord(bot, thread, "seul message relayé")
    assert (await next_chat(carol)).message == "[Discord] Théo: seul message relayé"


@requires_ap_server
async def test_bridge_while_disconnected(bot, room):
    runtime, thread, _ = room
    await runtime.tracker.stop()
    message = await say_on_discord(bot, thread, "personne ?")
    assert message.reactions == []


@requires_ap_server
async def test_server_console_messages_are_relayed(bot, room, ap_server):
    runtime, thread, _ = room
    ap_server.command("La partie reprend à 21h")
    await feed_contains(runtime, thread, "**Serveur** : La partie reprend à 21h")


@requires_ap_server
async def test_falls_back_to_signing_when_the_slot_refuses(bot, room):
    runtime, thread, carol = room
    await runtime.claim(2, 77)

    async def refuse(slot, text):
        raise APRefused(["InvalidPassword"])

    runtime.chat.say = refuse
    message = await say_on_discord(bot, thread, "mot de passe ?")
    chat = await next_chat(carol)
    assert (chat.slot, chat.message) == (1, "[Discord] Bob: mot de passe ?") and message.reactions == []
