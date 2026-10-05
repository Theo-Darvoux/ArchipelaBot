import asyncio
import contextlib
import logging
import time
from collections import defaultdict
from collections.abc import Callable

from ..ap.client import APSession, ConnectOptions, open_session
from .room import RoomTracker

log = logging.getLogger(__name__)

IDLE_TIMEOUT = 3600.0


class ChatRelay:
    """Speaks as the player who wrote on Discord: the server shows each message under the sender's slot."""

    def __init__(
        self,
        tracker: RoomTracker,
        password: str | None,
        *,
        idle_timeout: float = IDLE_TIMEOUT,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.tracker = tracker
        self.password = password
        self.idle_timeout = idle_timeout
        self.clock = clock
        self._sessions: dict[int, APSession] = {}
        self._drains: dict[int, asyncio.Task[None]] = {}
        self._last_used: dict[int, float] = {}
        self._locks: defaultdict[int, asyncio.Lock] = defaultdict(asyncio.Lock)

    async def say(self, slot: int, text: str) -> None:
        if slot == self.tracker.state.own_slot:
            await self.tracker.say(text)
            return
        session = await self._session(slot)
        await self.tracker.send_chat(session, slot, text)
        self._last_used[slot] = self.clock()

    async def _session(self, slot: int) -> APSession:
        async with self._locks[slot]:
            session = self._sessions.get(slot)
            if session is not None and not session.closed:
                return session
            options = ConnectOptions(
                slot=self.tracker.state.slot_name(slot),
                password=self.password,
                tags=("TextOnly", "NoText"),
                uuid=f"archipelabot-chat-{slot}",
            )
            online = self.tracker.state.is_online(slot)  # before our own connection marks the slot connected
            session = await open_session(self.tracker.state.address, options)
            self._sessions[slot] = session
            self.tracker.state.share(slot, online)
            self._drains[slot] = asyncio.create_task(self._drain(slot, session), name=f"chat relay {slot}")
            log.info("Opened a chat connection as %s", options.slot)
            return session

    async def _drain(self, slot: int, session: APSession) -> None:
        async for _ in session:
            pass
        if self._sessions.get(slot) is session:
            del self._sessions[slot]
            self.tracker.state.relay_slots.discard(slot)

    async def close_idle(self) -> None:
        now = self.clock()
        for slot, last in list(self._last_used.items()):
            if now - last >= self.idle_timeout:
                await self._close(slot)

    async def _close(self, slot: int) -> None:
        self._last_used.pop(slot, None)
        session = self._sessions.pop(slot, None)
        drain = self._drains.pop(slot, None)
        self.tracker.state.relay_slots.discard(slot)
        if session is not None:
            await session.close()
        if drain is not None:
            drain.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await drain

    async def close_all(self) -> None:
        for slot in list(self._sessions):
            await self._close(slot)

    async def run(self) -> None:
        while True:
            await asyncio.sleep(min(60.0, self.idle_timeout))
            await self.close_idle()
