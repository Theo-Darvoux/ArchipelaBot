"""Keeps a room's Progress up to date: baseline syncs, event log, periodic snapshots for the recap chart."""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import datetime

from ...core import events as ev
from ...core.progress import Baseline, Progress, utcnow
from ...core.room import RoomState
from ...storage.history import HistoryRepo, LoggedEvent, Snapshot

log = logging.getLogger(__name__)

type BaselineFetcher = Callable[[], Awaitable[Baseline]]
type ChangeCallback = Callable[[bool], None]  # urgent?


class ProgressService:
    def __init__(
        self,
        room_id: int,
        state: RoomState,
        progress: Progress,
        history: HistoryRepo,
        fetch_baseline: BaselineFetcher,
        on_change: ChangeCallback,
        *,
        periodic: bool,
        resync_interval: float = 300.0,
        retry_interval: float = 60.0,
        snapshot_interval: float = 300.0,
    ) -> None:
        self.room_id = room_id
        self.state = state
        self.progress = progress
        self.history = history
        self.fetch_baseline = fetch_baseline
        self.on_change = on_change
        self.periodic = periodic
        self.resync_interval = resync_interval
        self.retry_interval = retry_interval
        self.snapshot_interval = snapshot_interval
        self._connected = asyncio.Event()
        if state.connection == ev.ConnectionState.CONNECTED:
            self._connected.set()
        self._last_snapshot: dict[int, tuple[int, int | None]] = {}
        self.last_activity: datetime | None = None

    async def load(self) -> None:
        """Restore what only the bot knows (deaths, goal times) after a restart."""
        for event in await self.history.events(self.room_id):
            if event.slot is None:
                continue
            slot = self.progress[event.slot]
            if event.kind == "death":
                slot.deaths += 1
            elif event.kind == "goal" and slot.goal_at is None:
                slot.goal_at = event.at
        snapshots = await self.history.snapshots(self.room_id)
        self._last_snapshot = {s.slot: (s.checked, s.total) for s in snapshots}
        self.last_activity = max((s.at for s in snapshots), default=None)

    async def handle(self, event: ev.Event) -> None:
        changed = self.progress.apply(event)
        await self._log(event)
        match event:
            case ev.ConnectionChanged(state=ev.ConnectionState.CONNECTED):
                self._connected.set()
            case ev.ConnectionChanged():
                self._connected.clear()
            case ev.ClientStatusChanged() | ev.PlayerJoined() | ev.PlayerLeft():
                self.on_change(False)
            case ev.GoalReached():
                self.on_change(True)
                await self.snapshot()
            case ev.Released() | ev.Collected():
                self.on_change(False)
                await self.snapshot()
            case _ if changed:
                self.on_change(False)

    async def _log(self, event: ev.Event) -> None:
        now = utcnow()
        match event:
            case ev.GoalReached(slot=slot):
                logged = LoggedEvent(self.progress[slot].goal_at or now, "goal", slot)
            case ev.Death(source=source, cause=cause):
                logged = LoggedEvent(now, "death", self.state.slot_by_name(source), {"source": source, "cause": cause})
            case ev.Released(slot=slot, items=items):
                logged = LoggedEvent(now, "release", slot, {"count": len(items)})
            case ev.Collected(slot=slot, items=items):
                logged = LoggedEvent(now, "collect", slot, {"count": len(items)})
            case ev.HintAdded(hint=hint):
                logged = LoggedEvent(now, "hint", hint.receiver, {"item": hint.item, "finder": hint.finder})
            case _:
                return
        await self.history.log(self.room_id, logged)

    async def sync(self) -> None:
        self.progress.apply_baseline(await self.fetch_baseline())
        self.on_change(False)
        await self.snapshot()

    async def snapshot(self) -> None:
        now = utcnow()
        current = {s.slot: (self.progress[s.slot].done, self.progress[s.slot].total) for s in self.state.players}
        changed = [
            Snapshot(now, slot, done, total)
            for slot, (done, total) in current.items()
            if self._last_snapshot.get(slot) != (done, total)
        ]
        if changed:
            await self.history.snapshot(self.room_id, changed)
            self._last_snapshot.update(current)
            self.last_activity = now

    async def run(self) -> None:
        await asyncio.gather(self._sync_loop(), self._snapshot_loop())

    async def _sync_loop(self) -> None:
        while True:
            await self._connected.wait()
            try:
                await self.sync()
            except Exception:
                log.warning("Could not fetch the progress of %s", self.state.address, exc_info=True)
                await asyncio.sleep(self.retry_interval)
                continue
            if not self.periodic:
                return
            await asyncio.sleep(self.resync_interval)

    async def _snapshot_loop(self) -> None:
        while True:
            await asyncio.sleep(self.snapshot_interval)
            try:
                await self.snapshot()
            except Exception:
                log.exception("Could not save a progress snapshot")
