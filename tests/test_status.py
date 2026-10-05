"""Progress on the panel and /status, against a real Archipelago server."""

import asyncio

import discord
import pytest

from archipelabot.ap import protocol as p
from archipelabot.ap.client import ConnectOptions, open_session
from archipelabot.core import events as ev
from archipelabot.errors import UserError
from archipelabot.ui.panel_buttons import SettingsButton
from archipelabot.ui.rooms import RoomManager
from archipelabot.ui.views import SettingsView

from .ap_server import requires_ap_server
from .fakes import FakeInteraction, FakeUser, view_text
from .test_track_cog import discord_env, track  # noqa: F401

pytestmark = requires_ap_server

CAROL = ConnectOptions(slot="Carol", game="ChecksFinder", tags=(), items_handling=0b111, uuid="carol")


async def wait_until(predicate, wait=10.0):
    async with asyncio.timeout(wait):
        while not predicate():
            await asyncio.sleep(0.05)


@pytest.fixture
async def room(bot, discord_env, ap_server):  # noqa: F811
    guild, forum = discord_env
    await track(bot, guild, lien=ap_server.address, slot="Alice")
    [thread] = forum.threads
    runtime = bot.rooms.by_thread(thread.id)
    await wait_until(lambda: runtime.progress.total is not None)  # first baseline sync
    return runtime, thread, guild


async def status(bot, guild, thread, user=77, **kwargs) -> str:
    interaction = FakeInteraction(guild=guild, channel_id=thread.id, user=FakeUser(user))
    await bot.get_cog("status").status.callback(bot.get_cog("status"), interaction, **{"joueur": None, **kwargs})
    return "\n".join(view_text(reply["view"]) for reply in interaction.replies)


async def test_progress_from_baseline_and_live_checks(bot, room, ap_server):
    runtime, thread, guild = room
    total = runtime.progress.total
    assert total > 0 and runtime.progress.done == 0

    carol = await open_session(ap_server.address, CAROL)
    await carol.send(p.location_checks_packet(carol.connected.missing_locations[:4]))
    await wait_until(lambda: runtime.progress[3].done == 4)
    await wait_until(lambda: runtime.tracker.state.is_online(3))

    text = view_text(runtime.render_panel())
    assert f"**4 / {total}** checks" in text
    assert "· Carol · *ChecksFinder* · 🟢" in text

    # Without a claim, /status shows the whole panel; with a name, that player's details.
    assert f"**4 / {total}** checks" in await status(bot, guild, thread)
    detail = await status(bot, guild, thread, joueur="carol")
    assert detail.startswith("## Carol\n*ChecksFinder*")
    assert f"4 / {runtime.progress[3].total} checks · 🟢 en jeu" in detail

    await runtime.claim(2, 77)
    assert (await status(bot, guild, thread)).startswith("## Bob\n*A Short Hike* · joué par <@77>")
    with pytest.raises(UserError, match="Aucun joueur nommé"):
        await status(bot, guild, thread, joueur="Zelda")
    await carol.close()


async def test_deaths_and_goals_survive_a_restart(bot, room, ap_server):
    runtime, thread, _ = room
    carol = await open_session(ap_server.address, CAROL)
    await carol.send(
        {"cmd": "Bounce", "tags": ["DeathLink"], "data": p.death_link_data("Carol", "boom", 1.0)},
        p.status_update_packet(p.ClientStatus.GOAL),
    )
    await wait_until(lambda: runtime.progress[3].goal_at is not None and runtime.progress[3].deaths == 1)
    goal_at = runtime.progress[3].goal_at
    await carol.close()

    await bot.rooms.shutdown()
    bot.rooms = RoomManager(bot)
    await bot.rooms.restore()
    restored = bot.rooms.by_thread(thread.id)
    assert restored.progress[3].deaths == 1
    assert restored.progress[3].goal_at == goal_at
    assert [s.slot for s in await bot.rooms.history.snapshots(restored.record.id)] == [1, 2, 3]


async def test_direct_rooms_resync_after_a_connection_loss_but_not_a_restart(bot, room, ap_server):
    runtime, thread, _ = room
    carol = await open_session(ap_server.address, CAROL)
    await carol.send(p.location_checks_packet(carol.connected.missing_locations[:2]))
    await wait_until(lambda: runtime.progress[3].done == 2)
    total = runtime.progress.total

    await bot.rooms.shutdown()
    bot.rooms = RoomManager(bot)
    await bot.rooms.restore()
    restored = bot.rooms.by_thread(thread.id)
    syncs = 0
    fetch = restored.progress_service.fetch_baseline

    async def counting_fetch():
        nonlocal syncs
        syncs += 1
        return await fetch()

    restored.progress_service.fetch_baseline = counting_fetch
    # Known from the database before the room is even reached, without one connection per player.
    assert (restored.progress[3].done, restored.progress.total) == (2, total)
    await wait_until(lambda: restored.tracker.state.connection == ev.ConnectionState.CONNECTED)
    await asyncio.sleep(0.5)
    assert syncs == 0

    # Checks made while the bot was cut off are only known by asking again.
    await restored.tracker.reconnect()
    await carol.send(p.location_checks_packet(carol.connected.missing_locations[2:3]))
    await wait_until(lambda: syncs == 1 and restored.progress[3].done == 3)
    await carol.close()


async def test_settings_button_permissions(bot, room):
    runtime, _, _ = room
    button = SettingsButton(runtime.record.id)

    stranger = FakeInteraction(user=FakeUser(5))
    stranger.client = bot
    await button.callback(stranger)
    assert "Seule la personne" in view_text(stranger.response.sent[0]["view"])

    moderator = FakeInteraction(user=FakeUser(5), permissions=discord.Permissions(manage_threads=True))
    moderator.client = bot
    await button.callback(moderator)
    assert isinstance(moderator.response.sent[0]["view"], SettingsView)


async def test_releases_are_snapshotted_right_away(bot, room, ap_server):
    runtime, _, _ = room
    before = len(await bot.rooms.history.snapshots(runtime.record.id))
    ap_server.command("/release Carol")
    await wait_until(lambda: runtime.progress[3].ratio == 1.0)
    async with asyncio.timeout(5):
        while len(snaps := await bot.rooms.history.snapshots(runtime.record.id)) == before:
            await asyncio.sleep(0.1)
    assert any(s.slot == 3 and s.checked == s.total for s in snaps)
