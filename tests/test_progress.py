from datetime import UTC, datetime, timedelta

import aiohttp
from aiohttp import web
from aiohttp.test_utils import TestServer

from archipelabot.ap import protocol as p
from archipelabot.ap.client import ConnectOptions, open_session
from archipelabot.ap.protocol import ClientStatus, ItemFlags
from archipelabot.ap.webhost import WebhostClient, WebhostRoom
from archipelabot.core import events as ev
from archipelabot.core.progress import Baseline, Progress, direct_baseline, webhost_baseline
from archipelabot.core.room import RoomState, SlotInfo
from archipelabot.storage.history import HistoryRepo
from archipelabot.storage.rooms import RoomRecord, RoomRepo
from archipelabot.ui.services.progress import ProgressService, restore_progress

from .ap_server import requires_ap_server

T0 = datetime(2026, 10, 4, 20, 0, tzinfo=UTC)


def make_state() -> RoomState:
    return RoomState(
        "x:1",
        own_slot=1,
        slots={1: SlotInfo(1, "Alice", "OoT"), 2: SlotInfo(2, "Bob", "SM"), 3: SlotInfo(3, "Carol", "ALttP")},
    )


def sent(finder, receiver, location_id, flags=ItemFlags.PROGRESSION, name="Hookshot") -> ev.ItemSent:
    return ev.ItemSent(finder, receiver, name, f"Lieu {location_id}", flags, location_id)


def test_live_events_and_baseline_merge():
    now = [T0]
    progress = Progress(make_state(), clock=lambda: now[0])

    assert progress.apply(sent(1, 2, 101))
    assert progress.apply(sent(1, 1, 102, ItemFlags.FILLER))
    assert progress[1].checked == {101, 102} and progress[1].last_check == T0
    assert [i.item for i in progress[2].received] == ["Hookshot"]
    assert not progress[1].received  # self-found and filler items aren't "received"

    # The baseline was fetched a bit earlier: it doesn't know 102 yet, but knows older checks.
    progress.apply_baseline(Baseline({1: {100, 101}, 2: set()}, {1: 10, 2: 4, 3: 5}, {1: T0 - timedelta(hours=1)}))
    assert progress[1].checked == {100, 101, 102}
    assert progress[1].ratio == 0.3
    assert progress[1].last_check == T0  # the live value is more recent
    assert (progress.done, progress.total) == (3, 19)

    # A release checks everything left in the world, without counting as the player's activity.
    now[0] = T0 + timedelta(hours=2)
    assert progress.apply(ev.Released(3, tuple(sent(3, 1, 300 + i) for i in range(5))))
    assert progress[3].ratio == 1.0 and progress[3].last_check is None


def test_deaths_goals_and_ranking():
    now = [T0]
    state = make_state()
    progress = Progress(state, clock=lambda: now[0])
    progress.apply_baseline(Baseline({1: {1, 2}, 2: {1}, 3: set()}, {1: 4, 2: 4, 3: 4}))

    assert progress.apply(ev.Death("Bob", "fell"))
    assert not progress.apply(ev.Death("Somebody else", None))
    assert progress[2].deaths == 1

    assert progress.ranking() == [1, 2, 3]
    progress.apply(ev.GoalReached(3))
    now[0] = T0 + timedelta(minutes=5)
    progress.apply(ev.GoalReached(2))
    assert progress.ranking() == [3, 2, 1]
    assert not progress.apply(ev.GoalReached(3))  # the first goal time is kept
    assert progress[3].goal_at == T0

    # A goal reached before the bot started tracking only shows in the client status: it ranks first.
    state.statuses[1] = ClientStatus.GOAL
    assert progress.reached_goal(1) and progress.ranking() == [1, 3, 2]


async def test_webhost_baseline():
    last = "Sun, 04 Oct 2026 19:00:00 GMT"
    responses = {
        "/api/room_status/room": {"tracker": "trk", "last_port": 1, "players": [], "timeout": 1, "last_activity": last},
        "/api/static_tracker/trk": {"player_locations_total": [
            {"team": 0, "player": 1, "total_locations": 10}, {"team": 1, "player": 1, "total_locations": 99}]},
        "/api/tracker/trk": {
            "player_checks_done": [{"team": 0, "player": 1, "locations": [5, 6]}],
            "activity_timers": [{"team": 0, "player": 1, "time": last}, {"team": 0, "player": 2, "time": None}],
        },
    }  # fmt: skip

    requested: list[str] = []

    async def handler(request: web.Request) -> web.Response:
        requested.append(request.path)
        return web.json_response(responses[request.path])

    app = web.Application()
    app.router.add_get("/{tail:.*}", handler)
    async with TestServer(app) as server, aiohttp.ClientSession() as session:
        room = WebhostRoom(str(server.make_url("")).rstrip("/"), "room")
        baseline = await webhost_baseline(WebhostClient(session), room, team=0)
        assert baseline == Baseline({1: {5, 6}}, {1: 10}, {1: datetime(2026, 10, 4, 19, 0, tzinfo=UTC)})

        # Totals never change: the static tracker is only read once.
        requested.clear()
        assert await webhost_baseline(WebhostClient(session), room, team=0, totals=baseline.totals) == baseline
        assert "/api/static_tracker/trk" not in requested


@requires_ap_server
async def test_direct_baseline(ap_server):
    carol = await open_session(
        ap_server.address, ConnectOptions(slot="Carol", game="ChecksFinder", tags=(), uuid="carol")
    )
    locations = carol.connected.missing_locations[:3]
    await carol.send(p.location_checks_packet(locations))
    await carol.close()

    state = RoomState(ap_server.address, slots={1: SlotInfo(1, "Alice", ""), 3: SlotInfo(3, "Carol", "")})
    baseline = await direct_baseline(ap_server.address, None, state)
    assert baseline.checked == {1: set(), 3: set(locations)}
    assert baseline.totals[3] == len(carol.connected.missing_locations)
    assert baseline.totals[1] > 0


async def test_last_activity_follows_check_counts(db):
    room = await RoomRepo(db).create(RoomRecord(guild_id=1, name="R", address="x:1", slot="Alice", created_by=9))
    state = make_state()

    async def loaded() -> ProgressService:
        service = ProgressService(
            room.id, state, Progress(state), HistoryRepo(db), fetch_baseline=None, on_change=lambda urgent: None,
            periodic=False,
        )  # fmt: skip
        await service.load()
        return service

    service = await loaded()
    assert service.last_activity is None
    await service.snapshot()
    first = service.last_activity
    assert first is not None

    await service.snapshot()
    assert service.last_activity == first  # nothing moved

    service.progress.apply(sent(2, 1, 10))
    await service.snapshot()
    assert service.last_activity > first
    assert (await loaded()).last_activity == service.last_activity


async def test_restore_from_saved_slots_and_snapshots(db):
    room = await RoomRepo(db).create(RoomRecord(guild_id=1, name="R", address="x:1", slot="Alice", created_by=9))
    history = HistoryRepo(db)
    state = make_state()
    progress = Progress(state)
    progress.apply_baseline(Baseline({1: {1, 2, 3}, 2: {1}}, {1: 10, 2: 4, 3: 5}))
    state.statuses[2] = ClientStatus.GOAL
    service = ProgressService(
        room.id, state, progress, history, fetch_baseline=None, on_change=lambda urgent: None, periodic=False
    )
    await service.snapshot()
    await service.save_slots()

    # After a restart, before any connection: players, goals and counts are known again.
    restored_state = RoomState("x:1")
    restored = Progress(restored_state)
    await restore_progress(history, room.id, restored_state, restored)
    assert [s.name for s in restored_state.players] == ["Alice", "Bob", "Carol"]
    assert (restored.done, restored.total) == (4, 19) and restored.reached_goal(2)

    # A live check adds to the restored count; a baseline replaces it.
    restored.apply(sent(1, 2, 50))
    assert restored[1].done == 4
    restored.apply_baseline(Baseline({1: {1, 2, 3, 50}}, {}))
    assert restored[1].done == 4
