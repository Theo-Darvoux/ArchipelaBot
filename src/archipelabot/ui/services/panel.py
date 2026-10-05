"""Keeps a pinned message up to date without editing it more often than needed."""

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable

from discord import ui

log = logging.getLogger(__name__)


class PanelService:
    def __init__(
        self,
        render: Callable[[], ui.LayoutView],
        edit: Callable[[ui.LayoutView], Awaitable[None]],
        *,
        min_interval: float = 30.0,
        urgent_interval: float = 3.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.render = render
        self.edit = edit
        self.min_interval = min_interval
        self.urgent_interval = urgent_interval
        self.clock = clock
        self._dirty = asyncio.Event()
        self._urgent = False
        self._last_edit = float("-inf")

    def request_update(self, *, urgent: bool = False) -> None:
        """Urgent updates (connection state...) wait `urgent_interval` at most, others `min_interval`."""
        self._urgent |= urgent
        self._dirty.set()

    def _next_edit_at(self) -> float:
        return self._last_edit + (self.urgent_interval if self._urgent else self.min_interval)

    async def run(self) -> None:
        while True:
            await self._dirty.wait()
            while (delay := self._next_edit_at() - self.clock()) > 0:
                self._dirty.clear()
                try:
                    async with asyncio.timeout(delay):
                        await self._dirty.wait()  # a new request may be urgent: recompute the delay
                except TimeoutError:
                    break
            self._dirty.clear()
            self._urgent = False
            self._last_edit = self.clock()
            try:
                await self.edit(self.render())
            except Exception:
                log.exception("Could not update a panel")
