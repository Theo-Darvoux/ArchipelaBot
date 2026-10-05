import asyncio

import pytest

from archipelabot.ap import protocol as p
from archipelabot.ap.client import APRefused, ConnectOptions, open_session
from archipelabot.ap.datapackage import DataPackageStore
from archipelabot.core import events as ev
from archipelabot.core.room import RoomTracker

from .ap_server import APServer, requires_ap_server

pytestmark = requires_ap_server

BOT = ConnectOptions(slot="Alice", tags=("TextOnly", "DeathLink"))
ALICE, BOB, CAROL = 1, 2, 3


def game_client(slot: str, game: str) -> ConnectOptions:
    return ConnectOptions(slot=slot, game=game, tags=(), items_handling=0b111, uuid=f"game-{slot}")


class Recorder:
    def __init__(self) -> None:
        self.events: list[ev.Event] = []
        self._changed = asyncio.Event()

    async def __call__(self, event: ev.Event) -> None:
        self.events.append(event)
        self._changed.set()

    async def wait_for(self, predicate, wait: float = 10.0) -> ev.Event:
        async with asyncio.timeout(wait):
            while True:
                if match := next((e for e in self.events if predicate(e)), None):
                    return match
                self._changed.clear()
                await self._changed.wait()

    def of_type[T](self, cls: type[T]) -> list[T]:
        return [e for e in self.events if isinstance(e, cls)]


async def start_tracker(address: str, **kwargs) -> tuple[RoomTracker, Recorder]:
    tracker = RoomTracker(address, BOT, DataPackageStore(None), **kwargs)
    recorder = Recorder()
    tracker.subscribe(recorder)
    await tracker.start()
    return tracker, recorder


async def test_slots_and_items_with_names(ap_server):
    tracker, events = await start_tracker(ap_server.address)
    assert [(s.name, s.game) for s in tracker.state.players] == [
        ("Alice", "Celeste 64"),
        ("Bob", "A Short Hike"),
        ("Carol", "ChecksFinder"),
    ]
    assert events.events == [ev.ConnectionChanged(ev.ConnectionState.CONNECTED, ap_server.address)]

    carol = await open_session(ap_server.address, game_client("Carol", "ChecksFinder"))
    await events.wait_for(lambda e: e == ev.PlayerJoined(CAROL))
    await carol.send(p.location_checks_packet(carol.connected.missing_locations[:5]))

    await events.wait_for(lambda e: len(events.of_type(ev.ItemSent)) == 5)
    items = events.of_type(ev.ItemSent)
    assert all(i.finder == CAROL for i in items)
    assert all(not i.item.startswith("Item #") and not i.location.startswith("Lieu #") for i in items)
    assert ev.PlayerJoined(ALICE) not in events.events  # the bot's own TextOnly connection is not a player

    await carol.close()
    await events.wait_for(lambda e: e == ev.PlayerLeft(CAROL))
    await tracker.stop()
    assert events.events[-1].state == ev.ConnectionState.STOPPED


async def test_release_and_collect_are_folded(ap_server):
    tracker, events = await start_tracker(ap_server.address, bulk_quiet=0.5)

    ap_server.command("/release Carol")
    released = await events.wait_for(lambda e: isinstance(e, ev.Released))
    assert released.slot == CAROL and released.items
    assert all(i.finder == CAROL for i in released.items)

    ap_server.command("/collect Bob")
    collected = await events.wait_for(lambda e: isinstance(e, ev.Collected))
    assert collected.slot == BOB and collected.items
    assert all(i.receiver == BOB for i in collected.items)

    # Nothing leaked as individual item lines.
    assert not [i for i in events.of_type(ev.ItemSent) if i.finder == CAROL or i.receiver == BOB]
    await tracker.stop()


async def test_goal_chat_and_deathlink(ap_server):
    tracker, events = await start_tracker(ap_server.address)
    carol = await open_session(ap_server.address, game_client("Carol", "ChecksFinder"))

    await carol.send(
        p.say_packet("!hint"),
        p.say_packet("quelqu'un a le grappin ?"),
        {"cmd": "Bounce", "tags": ["DeathLink"], "data": p.death_link_data("Carol", "Carol a explosé", 1.0)},
        p.status_update_packet(p.ClientStatus.GOAL),
    )
    await tracker.say("[Discord] Théo: j'arrive")

    await events.wait_for(lambda e: e == ev.GoalReached(CAROL))
    assert events.of_type(ev.Death) == [ev.Death("Carol", "Carol a explosé")]
    # Commands and the bot's own bridged messages are not echoed back.
    assert events.of_type(ev.ChatMessage) == [ev.ChatMessage(CAROL, "quelqu'un a le grappin ?")]

    await carol.close()
    await tracker.stop()


async def test_reconnects_after_server_restart():
    server = APServer()
    tracker, events = await start_tracker(server.address, retry_delays=(0.2,))
    server.stop()
    await events.wait_for(lambda e: getattr(e, "state", None) == ev.ConnectionState.RECONNECTING)

    server = APServer(port=server.port)
    try:
        await events.wait_for(lambda e: events.events.count(e) == 2 and e.state == ev.ConnectionState.CONNECTED)
    finally:
        await tracker.stop()
        server.stop()


async def test_asleep_until_resolver_gives_a_port():
    server = APServer()
    answers: list[str | None] = [None, None]  # asleep twice, then the room is back

    async def resolve():
        return answers.pop(0) if answers else server.address

    tracker, events = await start_tracker(server.address, retry_delays=(0.1,), resolve_address=resolve)
    server.stop()
    await events.wait_for(lambda e: getattr(e, "state", None) == ev.ConnectionState.ASLEEP)
    assert len(events.of_type(ev.ConnectionChanged)) == 3  # connected, reconnecting, asleep (only once)

    server = APServer()  # the room comes back on another port
    try:
        await events.wait_for(lambda e: e == ev.ConnectionChanged(ev.ConnectionState.CONNECTED, server.address))
        assert tracker.state.address == server.address
    finally:
        await tracker.stop()
        server.stop()


async def test_refused_at_start(ap_server):
    tracker = RoomTracker(ap_server.address, ConnectOptions(slot="Nobody"), DataPackageStore(None))
    with pytest.raises(APRefused):
        await tracker.start()


async def test_resume_connects_in_background(ap_server):
    tracker = RoomTracker(ap_server.address, BOT, DataPackageStore(None), retry_delays=(5,))
    events = Recorder()
    tracker.subscribe(events)
    tracker.resume()
    await events.wait_for(lambda e: e == ev.ConnectionChanged(ev.ConnectionState.CONNECTED, ap_server.address), 3)
    assert tracker.state.players
    await tracker.stop()


async def test_client_statuses(ap_server):
    tracker, events = await start_tracker(ap_server.address)
    assert tracker.state.statuses[CAROL] == p.ClientStatus.UNKNOWN
    assert tracker.state.statuses[ALICE] == p.ClientStatus.CONNECTED  # the bot itself
    assert not tracker.state.is_online(ALICE)

    carol = await open_session(ap_server.address, game_client("Carol", "ChecksFinder"))
    await events.wait_for(lambda e: e == ev.ClientStatusChanged(CAROL, p.ClientStatus.CONNECTED))
    assert tracker.state.is_online(CAROL)

    await carol.send(p.status_update_packet(p.ClientStatus.GOAL))
    await events.wait_for(lambda e: e == ev.ClientStatusChanged(CAROL, p.ClientStatus.GOAL))
    await carol.close()
    assert tracker.state.statuses[CAROL] == p.ClientStatus.GOAL  # a goal is never undone
    await tracker.stop()


async def test_hints_added_prioritised_and_found(ap_server):
    tracker, events = await start_tracker(ap_server.address)
    assert tracker.state.hints == {}

    # Carol scouts one of her locations holding an item for someone else, announcing it as a hint.
    carol = await open_session(ap_server.address, game_client("Carol", "ChecksFinder"))
    await carol.send(p.location_scouts_packet(carol.connected.missing_locations))
    info = await anext(pk async for pk in carol if isinstance(pk, p.LocationInfo))
    target = next(i for i in info.locations if i.player != CAROL)
    await carol.send({"cmd": "LocationScouts", "locations": [target.location], "create_as_hint": 2})

    added = await events.wait_for(lambda e: isinstance(e, ev.HintAdded))
    hint = added.hint
    assert (hint.finder, hint.receiver, hint.location_id) == (CAROL, target.player, target.location)
    assert not hint.item.startswith("Item #") and not hint.found
    assert tracker.state.hints[hint.key] == hint

    # The receiver marks it as a priority: the list changes, but it's not a new hint.
    receiver = tracker.state.slots[target.player]
    games = {ALICE: "Celeste 64", BOB: "A Short Hike"}
    other = await open_session(ap_server.address, game_client(receiver.name, games[target.player]))
    await other.send({"cmd": "UpdateHint", "player": CAROL, "location": target.location, "status": 30})
    await events.wait_for(lambda e: tracker.state.hints[hint.key].status == p.HintStatus.PRIORITY)

    await carol.send(p.location_checks_packet([target.location]))
    await events.wait_for(lambda e: tracker.state.hints[hint.key].found)
    assert tracker.state.hints[hint.key].status == p.HintStatus.FOUND
    assert len(events.of_type(ev.HintAdded)) == 1

    # A tracker starting now loads the existing hints without announcing them.
    late, late_events = await start_tracker(ap_server.address)
    assert late.state.hints[hint.key].found and not late_events.of_type(ev.HintAdded)
    for session in (carol, other):
        await session.close()
    await tracker.stop()
    await late.stop()
