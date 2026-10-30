"""Tells players when a slot receives an item they asked for with /ping, in the room's post or by DM."""

import asyncio
import logging
from collections import defaultdict
from collections.abc import Callable
from typing import Protocol

from discord import ui

from ...core import events as ev
from ...core.room import RoomState
from ...storage.claims import NotifMode
from ..render.feed import Line, ping_line
from ..render.notify import received_items_view
from .feed import FeedSink, send_packed

log = logging.getLogger(__name__)

type Watchers = Callable[[ev.ItemSent], list[int]]  # users who asked for this item


class PingSink(FeedSink, Protocol):
    async def send_dm(self, user_id: int, view: ui.LayoutView) -> None: ...


class PingService:
    def __init__(
        self,
        state: RoomState,
        watchers: Watchers,
        mode: Callable[[int], NotifMode],
        plays: Callable[[int, int], bool],
        sink: PingSink,
        room_name: Callable[[], str],
        jump_url: Callable[[], str | None],
        *,
        interval: float = 3.0,
    ) -> None:
        self.state = state
        self.watchers = watchers
        self.mode = mode
        self.plays = plays
        self.sink = sink
        self.room_name = room_name
        self.jump_url = jump_url
        self.interval = interval
        self._pending: dict[int, list[ev.ItemSent]] = defaultdict(list)

    async def handle(self, event: ev.Event) -> None:
        match event:
            case ev.ItemSent():
                items: tuple[ev.ItemSent, ...] = (event,)
            case ev.Released(items=items) | ev.Collected(items=items):
                pass
            case _:
                return
        for item in items:
            if item.self_found:
                continue
            for user in self.watchers(item):
                self._pending[user].append(item)

    async def flush(self) -> None:
        pending, self._pending = self._pending, defaultdict(list)
        lines: list[Line] = []
        for user, items in pending.items():
            match self.mode(user):
                case NotifMode.THREAD:
                    lines += [ping_line(i, self.state, user, own=self.plays(user, i.receiver)) for i in items]
                case NotifMode.DM:
                    view = received_items_view(self.room_name(), items, self.state, self.jump_url())
                    try:
                        await self.sink.send_dm(user, view)
                    except Exception:
                        log.info("Could not DM user %s (DMs closed?)", user, exc_info=True)
        await send_packed(self.sink, lines)

    async def run(self) -> None:
        while True:
            await asyncio.sleep(self.interval)
            try:
                await self.flush()
            except Exception:
                log.exception("Could not send the pings of %s", self.state.address)
