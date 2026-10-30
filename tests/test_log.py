"""The full log forum and /historique, against a real Archipelago server."""

import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import discord
import pytest

from archipelabot.ap import protocol as p
from archipelabot.ap.client import open_session
from archipelabot.errors import UserError
from archipelabot.storage.history import LoggedEvent, LoggedItem
from archipelabot.ui.history import HistoryView, history_entries

from .ap_server import requires_ap_server
from .fakes import FakeForum, FakeInteraction, FakeUser, view_text
from .test_config_cog import choose, open_config
from .test_track_cog import CAROL, discord_env, track  # noqa: F401

T0 = datetime(2026, 10, 30, 12, 0, tzinfo=UTC)
NAMES = {1: "Alice", 2: "Bob", 3: "Carol"}.__getitem__


def test_history_entries_for_the_room_and_a_player():
    items = [
        LoggedItem(T0, 1, 2, "Hookshot", "Kakariko Well", int(p.ItemFlags.PROGRESSION)),
        LoggedItem(T0.replace(minute=5), 3, 3, "Bow", "Shop", int(p.ItemFlags.USEFUL)),
    ]
    events = [
        LoggedEvent(T0.replace(minute=2), "death", 2, {"source": "Bob", "cause": "lava"}),
        LoggedEvent(T0.replace(minute=7), "goal", 3),
        LoggedEvent(T0.replace(minute=8), "hint", 1, {"item": "Lens", "finder": 3}),
    ]
    entries = history_entries(items, events, NAMES)
    assert [e.text for e in entries] == [
        "Alice → Hookshot → Bob · Kakariko Well",
        "Mort de Bob · « lava »",
        "Carol a trouvé Bow · Shop",
        "Carol a terminé son jeu",
        "Hint : Lens pour Alice · chez Carol",
    ]
    assert [e.text for e in history_entries(items, events, NAMES, slot=2)] == [
        "Alice → Hookshot → Bob · Kakariko Well",
        "Mort de Bob · « lava »",
    ]


async def test_history_view_pages_newest_first_and_exports_everything():
    items = [LoggedItem(T0.replace(minute=i), 1, 2, f"Item {i}", "Lieu", 0) for i in range(40)]
    view = HistoryView("Async", history_entries(items, [], NAMES), ZoneInfo("Europe/Paris"))
    text = view_text(view)
    assert "40 événements" in text and "`30/10 13:39`" in text and "Item 39" in text and "Item 24" not in text
    view.to_components()

    def buttons():
        return [b for b in view.walk_children() if isinstance(b, discord.ui.Button)]

    previous, counter, _, _ = buttons()
    assert previous.disabled and counter.label == "1/3"
    await buttons()[2].callback(FakeInteraction())
    await buttons()[2].callback(FakeInteraction())
    _, counter, following, download = buttons()
    assert "Item 0" in view_text(view) and following.disabled and counter.label == "3/3"

    interaction = FakeInteraction()
    await download.callback(interaction)
    [file] = interaction.response.sent[0]["files"]
    lines = file.fp.read().decode().splitlines()
    assert lines[0] == "Async" and len(lines) == 42
    assert lines[2] == "2026-10-30 13:00 Alice → Item 0 → Bob · Lieu"


@pytest.fixture
async def log_env(bot, discord_env):  # noqa: F811
    guild, forum = discord_env
    log_forum = FakeForum(id=200, guild=guild)
    guild.channels[log_forum.id] = log_forum
    config = await bot.guild_configs.get(guild.id)
    config.log_forum_id = log_forum.id
    await bot.guild_configs.save(config)
    room_lookup = bot.get_channel

    def get_channel(channel_id):
        return room_lookup(channel_id) or next((t for t in log_forum.threads if t.id == channel_id), None)

    bot.get_channel = get_channel
    return guild, forum, log_forum


async def history(bot, guild, channel_id, joueur=None) -> str:
    cog = bot.get_cog("history")
    interaction = FakeInteraction(guild=guild, channel_id=channel_id, user=FakeUser(77))
    await cog.history.callback(cog, interaction, joueur=joueur)
    return view_text(interaction.response.sent[0]["view"])


@requires_ap_server
async def test_items_go_to_the_full_log_and_into_the_history(bot, log_env, ap_server):
    guild, forum, log_forum = log_env
    await track(bot, guild, lien=ap_server.address, slot="Alice", nom="Async")
    [thread] = forum.threads
    runtime = bot.rooms.by_thread(thread.id)
    assert log_forum.threads == []  # created with its first lines

    carol = await open_session(ap_server.address, CAROL)
    await carol.send(p.location_checks_packet(carol.connected.missing_locations[:3]), p.say_packet("coucou"))
    async with asyncio.timeout(10):
        while not (log_forum.threads and log_forum.threads[0].texts() and any("coucou" in t for t in thread.texts())):
            await runtime.flush()
            await asyncio.sleep(0.1)
    await carol.close()

    [log_thread] = log_forum.threads
    assert log_thread.name == "Async"
    assert f"<#{thread.id}>" in log_thread.messages[0].text
    assert "Carol" in "\n".join(log_thread.texts())
    assert not any("→" in t or "a trouvé" in t for t in thread.texts())  # the room's post only has the chat
    assert (await bot.rooms.repo.get(runtime.record.id)).log_thread_id == log_thread.id
    assert "[suivi complet](https://discord.com/channels/1/" in view_text(runtime.render_panel())

    async with asyncio.timeout(5):
        while not await bot.rooms.history.items(runtime.record.id):
            await asyncio.sleep(0.05)
    text = await history(bot, guild, thread.id, joueur="carol")
    assert "Async · Carol" in text and "Carol" in text
    assert "Carol" in await history(bot, guild, log_thread.id)

    await bot.rooms.stop(runtime, "finished")
    assert "Carol" in await history(bot, guild, log_thread.id)  # still readable once the room is stopped
    with pytest.raises(UserError, match="Aucun joueur nommé"):
        await history(bot, guild, thread.id, joueur="Zelda")
    with pytest.raises(UserError, match="Utilise cette commande"):
        await history(bot, guild, 12345)


async def test_config_log_forum(bot, log_env):
    guild, _, log_forum = log_env
    view = await open_config(bot, guild)
    text = await choose(view, "suivi", SimpleNamespace(id=log_forum.id), guild=guild)
    assert "suivi complet dans <#200>" in text and "**Suivi complet** · <#200>" in text
    assert (await bot.guild_configs.get(guild.id)).log_forum_id == log_forum.id

    log_forum.permissions = discord.Permissions(view_channel=True)
    text = await choose(view, "suivi", SimpleNamespace(id=log_forum.id), guild=guild)
    assert "⚠️" in text and "send_messages" in text

    text = await choose(view, "suivi", guild=guild)
    assert (await bot.guild_configs.get(guild.id)).log_forum_id is None
    assert "**Suivi complet** · *aucun*" in text
