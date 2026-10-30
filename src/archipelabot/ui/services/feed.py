"""Posts room events, grouping lines to stay well under Discord's rate limits."""

import asyncio
import logging
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass
from typing import Protocol

from discord import ui

from ...core import events as ev
from ..render.feed import Card, FeedOutput, Line, connection_line

log = logging.getLogger(__name__)

# A message holds at most 40 components (a line and a divider each) and 4000 characters of text.
MAX_LINES = 20
MAX_CHARS = 3800

type Renderer = Callable[[ev.Event], list[FeedOutput]]


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
        if line.mention is not None:
            mentions[line.mention] = None
    if texts:
        yield FeedMessage(tuple(texts), tuple(mentions))


async def send_packed(sink: FeedSink, lines: list[Line]) -> None:
    """Ping each user in the first message that mentions them only."""
    pinged: set[int] = set()
    for message in pack_lines(lines):
        mentions = tuple(u for u in message.mentions if u not in pinged)
        pinged.update(mentions)
        await sink.send_lines(FeedMessage(message.lines, mentions))


class FeedService:
    def __init__(
        self,
        address: str,
        render: Renderer,
        sink: FeedSink,
        *,
        connections: bool = False,
        interval: float = 3.0,
    ) -> None:
        self.render = render
        self.sink = sink
        self.connections = connections
        self.interval = interval
        self._pending: list[FeedOutput] = []
        self._address = address
        self._told_asleep = False

    async def handle(self, event: ev.Event) -> None:
        if isinstance(event, ev.ConnectionChanged):
            if not self.connections:
                return
            if event.state == ev.ConnectionState.ASLEEP:
                if self._told_asleep:
                    return
                self._told_asleep = True
            if line := connection_line(event, self._address_changed(event)):
                self._pending.append(line)
        elif outputs := self.render(event):
            self._told_asleep = False
            self._pending.extend(outputs)

    def _address_changed(self, event: ev.ConnectionChanged) -> bool:
        if event.state != ev.ConnectionState.CONNECTED or event.address == self._address:
            return False
        self._address = event.address
        return True

    async def flush(self) -> None:
        pending, self._pending = self._pending, []
        lines: list[Line] = []
        for output in pending:
            match output:
                case Line():
                    lines.append(output)
                case Card():
                    await send_packed(self.sink, lines)
                    lines = []
                    await self.sink.send_view(output.view())
        await send_packed(self.sink, lines)

    async def run(self) -> None:
        while True:
            await asyncio.sleep(self.interval)
            try:
                await self.flush()
            except Exception:
                log.exception("Could not post the feed of %s", self._address)
