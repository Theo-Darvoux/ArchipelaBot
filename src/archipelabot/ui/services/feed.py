"""Posts room events in the forum post, grouping lines to stay well under Discord's rate limits."""

import asyncio
import logging
import time
from collections import defaultdict
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass
from typing import Protocol

from discord import ui

from ...core import events as ev
from ...core.room import RoomState
from ...storage.rooms import RoomSettings
from ..render.feed import (
    Card,
    FeedOutput,
    Line,
    Ping,
    Pinger,
    connection_line,
    no_ping,
    render_event,
    waiting_line,
)
from .presence import Held, Presence

log = logging.getLogger(__name__)

# A message holds at most 40 components (a line and a divider each) and 4000 characters of text.
MAX_LINES = 20
MAX_CHARS = 3800


@dataclass(frozen=True, slots=True)
class FeedMessage:
    lines: tuple[str, ...]
    mentions: tuple[int, ...] = ()


class FeedSink(Protocol):
    async def send_lines(self, message: FeedMessage) -> None: ...
    async def send_view(self, view: ui.LayoutView) -> None: ...


def pack_lines(
    lines: Iterable[Line], *, max_lines: int = MAX_LINES, max_chars: int = MAX_CHARS
) -> Iterator[FeedMessage]:
    """Group lines into as few messages as possible, each carrying the mentions of its lines."""
    texts: list[str] = []
    mentions: dict[int, None] = {}
    size = 0
    for line in lines:
        text = line.text if len(line.text) <= max_chars else line.text[: max_chars - 1] + "…"
        if texts and (len(texts) == max_lines or size + len(text) > max_chars):
            yield FeedMessage(tuple(texts), tuple(mentions))
            texts, mentions, size = [], {}, 0
        texts.append(text)
        size += len(text)
        if line.ping:
            mentions[line.ping.user] = None
    if texts:
        yield FeedMessage(tuple(texts), tuple(mentions))


class FeedService:
    def __init__(
        self,
        state: RoomState,
        settings: Callable[[], RoomSettings],
        sink: FeedSink,
        *,
        ping: Pinger = no_ping,
        presence: Presence | None = None,
        interval: float = 3.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.state = state
        self.settings = settings
        self.sink = sink
        self.ping = ping
        self.interval = interval
        self.clock = clock
        self.presence = presence or Presence(state)
        self._pending: list[FeedOutput] = []
        self._held: Held = defaultdict(list)
        self._down_since: float | None = None

    async def handle(self, event: ev.Event) -> None:
        if isinstance(event, ev.ConnectionChanged):
            if line := connection_line(event, self._downtime(event.state)):
                self._pending.append(line)
        else:
            self._pending.extend(render_event(event, self.state, self.settings(), self.ping))

    def _downtime(self, state: ev.ConnectionState) -> float | None:
        if state == ev.ConnectionState.CONNECTED:
            down_since, self._down_since = self._down_since, None
            return None if down_since is None else self.clock() - down_since
        if state in (ev.ConnectionState.RECONNECTING, ev.ConnectionState.ASLEEP) and self._down_since is None:
            self._down_since = self.clock()
        return None

    async def flush(self) -> None:
        pending, self._pending = self._pending, []
        lines, allowed = self._pings(pending)
        for output in pending:
            match output:
                case Line():
                    lines.append(output)
                case Card():
                    await self._send_lines(lines, allowed)
                    lines = []
                    await self.sink.send_view(output.view())
        await self._send_lines(lines, allowed)

    def _pings(self, pending: list[FeedOutput]) -> tuple[list[Line], set[int]]:
        """Who to ping in this flush, and a line for the items that waited for their player to be away."""
        new = [i for o in pending if isinstance(o, Line) and o.ping for i in o.ping.items]
        for item in new:
            self._held[item.receiver].append(item)
        new_ids = {id(i) for i in new}
        allowed: set[int] = set()
        waited: dict[int, list[ev.ItemSent]] = defaultdict(list)
        for item in self.presence.release(self._held):
            if (user := self.ping(item.receiver)) is not None:
                allowed.add(user)
                if id(item) not in new_ids:
                    waited[user].append(item)
        return [waiting_line(Ping(user, tuple(items))) for user, items in waited.items()], allowed

    async def _send_lines(self, lines: list[Line], allowed: set[int]) -> None:
        """Ping each allowed user in the first message that mentions them only."""
        for message in pack_lines(lines):
            mentions = tuple(u for u in message.mentions if u in allowed)
            allowed.difference_update(mentions)
            await self.sink.send_lines(FeedMessage(message.lines, mentions))

    async def run(self) -> None:
        while True:
            await asyncio.sleep(self.interval)
            try:
                await self.flush()
            except Exception:
                log.exception("Could not post the feed of %s", self.state.address)
