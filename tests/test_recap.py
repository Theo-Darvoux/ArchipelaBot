import asyncio
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import discord
import pytest

from archipelabot.ap import protocol as p
from archipelabot.ap.client import ConnectOptions, open_session
from archipelabot.core import events as ev
from archipelabot.core.progress import Baseline, Progress
from archipelabot.core.room import RoomState, SlotInfo
from archipelabot.errors import UserError
from archipelabot.recap.chart import render_chart
from archipelabot.recap.stats import build_recap
from archipelabot.storage.guilds import GuildConfig
from archipelabot.storage.history import LoggedEvent, Snapshot
from archipelabot.ui.emojis import E
from archipelabot.ui.render.recap import duration, recap_view

from .ap_server import requires_ap_server
from .fakes import FakeInteraction, FakeUser, view_text
from .test_track_cog import discord_env, track  # noqa: F401

T0 = datetime(2026, 10, 4, 20, 0, tzinfo=UTC)


def make_game():
    now = [T0]
    state = RoomState(
        "x:1",
        slots={1: SlotInfo(1, "Alice", "OoT"), 2: SlotInfo(2, "Bob", "SM"), 3: SlotInfo(3, "Carol", "ALttP")},
    )
    progress = Progress(state, clock=lambda: now[0])
    progress.apply_baseline(Baseline({1: set(range(10)), 2: set(range(5)), 3: set(range(2))}, {1: 10, 2: 10, 3: 10}))
    now[0] = T0 + timedelta(hours=3, minutes=5)
    progress.apply(ev.GoalReached(1))
    events = [
        LoggedEvent(T0, "death", 2),
        LoggedEvent(T0, "death", 2),
        LoggedEvent(T0, "death", 3),
        LoggedEvent(T0, "hint", 1),
        LoggedEvent(T0, "release", 1),
    ]
    snapshots = [Snapshot(T0, 1, 2, 10), Snapshot(T0, 2, 0, 10), Snapshot(T0 + timedelta(hours=1), 1, 6, 10)]
    return state, progress, events, snapshots


def test_build_recap():
    state, progress, events, snapshots = make_game()
    recap = build_recap("Async", T0, T0 + timedelta(hours=4), state, progress, events, snapshots)

    assert [f.slot for f in recap.finishers] == [1]
    assert recap.unfinished == [(2, 0.5), (3, 0.2)]
    assert (recap.checks_done, recap.checks_total) == (17, 30)
    assert (recap.deaths, recap.death_champion, recap.hints, recap.releases) == (3, (2, 2), 1, 1)
    alice = next(s for s in recap.series if s.slot == 1)
    assert [r for _, r in alice.points] == [0.2, 0.6, 1.0] and alice.goal_at == T0 + timedelta(hours=3, minutes=5)
    assert {s.slot for s in recap.series} == {1, 2, 3}  # Carol has no snapshot, only her final value


def test_duration():
    assert duration(timedelta(minutes=7)) == "7 min"
    assert duration(timedelta(hours=3, minutes=5)) == "3 h 05 min"
    assert duration(timedelta(days=2, hours=4, minutes=59)) == "2 j 4 h"


def test_recap_view_and_chart():
    state, progress, events, snapshots = make_game()
    recap = build_recap("Async", T0, T0 + timedelta(hours=4), state, progress, events, snapshots)
    text = view_text(recap_view(recap, state, chart=True))
    assert text.startswith("## 🏆 Récap\n-# Async · 3 joueurs · suivie pendant 4 h 00 min")
    assert "🥇 **Alice** · *OoT* · en 3 h 05 min" in text
    assert "◑ Bob · *SM* · 50 %" in text
    assert "💀 3 morts · record : **Bob** (2)" in text
    assert "📤 1 release · 📥 0 collect" in text

    png = render_chart(recap, state.name, ZoneInfo("Europe/Paris"))
    assert png.startswith(b"\x89PNG") and len(png) > 10_000


CLIENTS = {"Alice": "Celeste 64", "Bob": "A Short Hike", "Carol": "ChecksFinder"}


class RecapChannel:
    id = 4242

    def __init__(self) -> None:
        self.sent: list[dict] = []

    async def send(self, **kwargs) -> None:
        self.sent.append(kwargs)


@requires_ap_server
async def test_everyone_finishing_publishes_the_recap_and_ends_the_room(bot, discord_env, ap_server):  # noqa: F811
    guild, forum = discord_env
    recap_channel = RecapChannel()
    guild.channels[recap_channel.id] = recap_channel
    await bot.guild_configs.save(GuildConfig(guild.id, forum_id=forum.id, recap_channel_id=recap_channel.id))
    await track(bot, guild, lien=ap_server.address, slot="Alice", nom="Finale")
    [thread] = forum.threads
    runtime = bot.rooms.by_thread(thread.id)

    for name, game in CLIENTS.items():
        client = await open_session(ap_server.address, ConnectOptions(slot=name, game=game, tags=(), uuid=name))
        await client.send(p.status_update_packet(p.ClientStatus.GOAL))
        await asyncio.sleep(0.2)
        await client.close()

    async with asyncio.timeout(15):
        while bot.rooms.by_thread(thread.id) is not None:
            await asyncio.sleep(0.1)

    [recap] = [m for m in thread.messages if "Partie terminée" in m.text]
    assert "🥇 **Alice**" in recap.text and "🥉 **Carol**" in recap.text
    assert (await bot.rooms.repo.get(runtime.record.id)).status == "finished"
    assert [t.name for t in thread.applied_tags] == ["Terminée"]

    [posted] = recap_channel.sent
    assert f"[voir la room](https://discord.com/channels/{guild.id}/{thread.id})" in view_text(posted["view"])
    assert posted["files"] and posted["files"][0].filename == "recap.png"


@requires_ap_server
async def test_recap_command_posts_in_the_thread(bot, discord_env, ap_server):  # noqa: F811
    guild, forum = discord_env
    recap_channel = RecapChannel()
    guild.channels[recap_channel.id] = recap_channel
    await bot.guild_configs.save(GuildConfig(guild.id, forum_id=forum.id, recap_channel_id=recap_channel.id))
    await track(bot, guild, lien=ap_server.address, slot="Alice")
    [thread] = forum.threads
    cog = bot.get_cog("status")
    with pytest.raises(UserError, match="Seule la personne"):
        await cog.recap.callback(cog, FakeInteraction(guild=guild, channel_id=thread.id, user=FakeUser(5)))

    interaction = FakeInteraction(guild=guild, channel_id=thread.id)
    await cog.recap.callback(cog, interaction)
    assert "Récap publié" in view_text(interaction.followup.sent[0]["view"])
    assert any(m.text.startswith("## 🏆 Récap") for m in thread.messages)
    assert bot.rooms.by_thread(thread.id) is not None  # the room keeps being tracked
    assert not recap_channel.sent  # only the end of the game is announced


@requires_ap_server
async def test_recap_of_a_stopped_room(bot, discord_env, ap_server):  # noqa: F811
    guild, forum = discord_env
    await track(bot, guild, lien=ap_server.address, slot="Alice", nom="Finie")
    [thread] = forum.threads
    runtime = bot.rooms.by_thread(thread.id)
    await asyncio.wait_for(_until(lambda: runtime.progress.total is not None), 10)
    carol = await open_session(ap_server.address, ConnectOptions(slot="Carol", game="ChecksFinder", tags=()))
    await carol.send(
        p.location_checks_packet(carol.connected.missing_locations[:2]), p.status_update_packet(p.ClientStatus.GOAL)
    )
    await asyncio.wait_for(_until(lambda: runtime.progress[3].goal_at is not None), 10)
    await carol.close()
    await bot.rooms.stop(runtime, "stopped")

    cog = bot.get_cog("status")
    interaction = FakeInteraction(
        guild=guild, channel_id=thread.id, permissions=discord.Permissions(manage_threads=True)
    )
    await cog.recap.callback(cog, interaction)
    [recap] = [m for m in thread.messages if m.text.startswith("## 🏆 Récap")]
    assert "Finie · 3 joueurs" in recap.text
    assert "🥇 **Carol** · *ChecksFinder* · en " in recap.text and "Alice · *Celeste 64* · 0 %" in recap.text

    with pytest.raises(UserError, match="dans le post d'une room"):
        await cog.recap.callback(cog, FakeInteraction(guild=guild, channel_id=12345))


async def _until(predicate) -> None:
    while not predicate():
        await asyncio.sleep(0.05)


def test_recap_of_a_big_room_fits_in_one_message():
    long_names = {i: SlotInfo(i, f"Joueur_{i:03}_xxxxx", "A Link to the Past Randomizer") for i in range(1, 81)}
    state = RoomState("x:1", slots=long_names)
    progress = Progress(state, clock=lambda: T0)
    progress.apply_baseline(Baseline({i: set(range(i)) for i in long_names}, dict.fromkeys(long_names, 100)))
    recap = build_recap("Gros async", T0, T0 + timedelta(days=3), state, progress, [], [])
    # Uploaded icons are long custom emoji tags: render with those.
    E.use({f"ring_{i}": discord.PartialEmoji(name=f"ap_ring_{i}_abcdef", id=10**18 + i) for i in range(9)})
    try:
        text = view_text(recap_view(recap, state, chart=True))
    finally:
        E.reset()
    assert len(text) <= 4000 and "autres joueurs" in text
