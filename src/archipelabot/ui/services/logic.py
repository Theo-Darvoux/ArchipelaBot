import asyncio
import logging
from collections.abc import Awaitable, Callable

from ...core.logic import LogicWorker, SlotLogic
from ...core.progress import Progress
from ...core.room import RoomState

log = logging.getLogger(__name__)


class LogicService:
    """Who can do what, recomputed by a regenerated seed once checks stop coming in for `debounce` seconds."""

    def __init__(
        self,
        state: RoomState,
        progress: Progress,
        sync: Callable[[], Awaitable[None]],
        on_change: Callable[[], None],
        *,
        debounce: float = 2.0,
    ) -> None:
        self.state = state
        self.progress = progress
        self.sync = sync
        self.on_change = on_change
        self.debounce = debounce
        self.results: dict[int, SlotLogic] = {}
        self._worker: LogicWorker | None = None
        self._signature: dict[int, int] | None = None
        self._dirty = asyncio.Event()

    @property
    def active(self) -> bool:
        return self._worker is not None

    async def use(self, worker: LogicWorker) -> None:
        old, self._worker = self._worker, worker
        self._signature, self.results = None, {}
        if old is not None:
            await old.close()
        try:
            await self.refresh()
        except Exception:
            log.warning("Could not compute the logic of %s", self.state.address, exc_info=True)
            self.request()

    def request(self) -> None:
        if self._worker is not None:
            self._dirty.set()

    async def run(self) -> None:
        while True:
            await self._dirty.wait()
            await asyncio.sleep(self.debounce)
            self._dirty.clear()
            try:
                await self.refresh()
            except Exception:
                log.warning("Could not compute the logic of %s", self.state.address, exc_info=True)

    async def refresh(self) -> None:
        worker = self._worker
        if worker is None:
            return
        if not self.progress.synced:
            await self.sync()  # the logic needs which locations are checked, not how many
        players = [s.slot for s in self.state.players]
        signature = {slot: self.progress[slot].done for slot in players}
        if signature == self._signature:
            return
        self.results = await worker.evaluate({slot: self.progress[slot].checked for slot in players})
        self._signature = signature
        self.on_change()

    async def inventory(self, slot: int) -> list[tuple[str, str]] | None:
        """Progression items the logic counts for a slot, with where they come from; None without logic."""
        if self._worker is None or not self.progress.synced:
            return None
        players = [s.slot for s in self.state.players]
        return await self._worker.inventory({s: self.progress[s].checked for s in players}, slot)

    async def close(self) -> None:
        worker, self._worker = self._worker, None
        self.results = {}
        if worker is not None:
            await worker.close()
