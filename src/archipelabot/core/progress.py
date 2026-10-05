import logging
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime

from ..ap.client import ConnectOptions, open_session
from ..ap.protocol import ClientStatus, ItemFlags
from ..ap.webhost import WebhostClient, WebhostRoom, parse_http_date
from . import events as ev
from .room import RoomState

log = logging.getLogger(__name__)

RECENT_ITEMS = 10


def utcnow() -> datetime:
    return datetime.now(UTC)


@dataclass
class SlotProgress:
    checked: set[int] = field(default_factory=set)
    total: int | None = None
    last_check: datetime | None = None
    deaths: int = 0
    goal_at: datetime | None = None
    received: deque[ev.ItemSent] = field(default_factory=lambda: deque(maxlen=RECENT_ITEMS))

    @property
    def done(self) -> int:
        return len(self.checked)

    @property
    def ratio(self) -> float | None:
        if not self.total:
            return None
        return min(1.0, self.done / self.total)


@dataclass(frozen=True, slots=True)
class Baseline:
    checked: dict[int, set[int]]
    totals: dict[int, int]
    last_activity: dict[int, datetime] = field(default_factory=dict)


class Progress:
    def __init__(self, state: RoomState, clock: Callable[[], datetime] = utcnow) -> None:
        self.state = state
        self.clock = clock
        self._slots: dict[int, SlotProgress] = {}

    def __getitem__(self, slot: int) -> SlotProgress:
        return self._slots.setdefault(slot, SlotProgress())

    @property
    def done(self) -> int:
        return sum(self[s.slot].done for s in self.state.players)

    @property
    def total(self) -> int | None:
        totals = [self[s.slot].total for s in self.state.players]
        return None if any(t is None for t in totals) else sum(totals)  # type: ignore[misc]

    def reached_goal(self, slot: int) -> bool:
        return self[slot].goal_at is not None or self.state.statuses.get(slot) == ClientStatus.GOAL

    def ranking(self) -> list[int]:
        """Finished players first (earliest goal first), then by completion."""
        far_past = datetime.min.replace(tzinfo=UTC)

        def key(slot: int) -> tuple:
            progress = self[slot]
            if self.reached_goal(slot):
                return (0, progress.goal_at or far_past, 0.0)
            return (1, far_past, -(progress.ratio or 0.0))

        return sorted((s.slot for s in self.state.players), key=key)

    def apply(self, event: ev.Event) -> bool:
        """Update from a room event; returns whether anything changed."""
        now = self.clock()
        match event:
            case ev.ItemSent():
                changed = self._check(event, now)
                if not event.self_found and event.flags & ItemFlags.PROGRESSION:
                    self[event.receiver].received.append(event)
                return changed
            case ev.Released(items=items) | ev.Collected(items=items):
                return any([self._check(item, None) for item in items])
            case ev.Death(source=source):
                if (slot := self.state.slot_by_name(source)) is not None:
                    self[slot].deaths += 1
                    return True
            case ev.GoalReached(slot=slot) if self[slot].goal_at is None:
                self[slot].goal_at = now
                return True
        return False

    def _check(self, item: ev.ItemSent, at: datetime | None) -> bool:
        progress = self[item.finder]
        if at is not None:
            progress.last_check = at
        if not item.location_id or item.location_id in progress.checked:
            return at is not None
        progress.checked.add(item.location_id)
        return True

    def apply_baseline(self, baseline: Baseline) -> None:
        for slot, checked in baseline.checked.items():
            self[slot].checked |= checked
        for slot, total in baseline.totals.items():
            self[slot].total = total
        for slot, at in baseline.last_activity.items():
            progress = self[slot]
            if progress.last_check is None or at > progress.last_check:
                progress.last_check = at


async def webhost_baseline(client: WebhostClient, room: WebhostRoom, team: int) -> Baseline:
    status = await client.room_status(room)
    if not status.tracker:
        raise ValueError("this room has no tracker")
    static = await client.static_tracker(room, status.tracker)
    tracker = await client.tracker(room, status.tracker)

    def ours(rows: list[dict]) -> list[dict]:
        return [row for row in rows if row.get("team", 0) == team]

    return Baseline(
        checked={row["player"]: set(row["locations"]) for row in ours(tracker.get("player_checks_done", []))},
        totals={row["player"]: row["total_locations"] for row in ours(static.get("player_locations_total", []))},
        last_activity={
            row["player"]: parse_http_date(row["time"])
            for row in ours(tracker.get("activity_timers", []))
            if row.get("time")
        },
    )


async def direct_baseline(address: str, password: str | None, state: RoomState) -> Baseline:
    checked: dict[int, set[int]] = {}
    totals: dict[int, int] = {}
    for info in state.players:
        options = ConnectOptions(slot=info.name, password=password, tags=("Tracker",), uuid="archipelabot-baseline")
        session = await open_session(address, options)
        try:
            connected = session.connected
            checked[info.slot] = set(connected.checked_locations)
            totals[info.slot] = len(connected.checked_locations) + len(connected.missing_locations)
        finally:
            await session.close()
    return Baseline(checked, totals)
