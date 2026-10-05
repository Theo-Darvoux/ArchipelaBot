"""Players are notified of received items only while away from the game, once per absence."""

import math
import time
from collections.abc import Callable

from ...ap.protocol import ClientStatus
from ...core import events as ev
from ...core.room import RoomState

GRACE = 120.0

type Held = dict[int, list[ev.ItemSent]]  # receiving slot -> items


class Presence:
    """Game clients joining and leaving tell when a player is away; the bot's own connections to slots don't count."""

    def __init__(self, state: RoomState, *, grace: float = GRACE, clock: Callable[[], float] = time.monotonic) -> None:
        self.state = state
        self.grace = grace
        self.clock = clock
        self._playing: set[int] = set()
        self._away_since: dict[int, float] = {}
        self._notified: set[int] = set()

    async def handle(self, event: ev.Event) -> None:
        match event:
            case ev.PlayerJoined(slot=slot):
                self._playing.add(slot)
                self._away_since.pop(slot, None)
                self._notified.discard(slot)
            case ev.PlayerLeft(slot=slot):
                self._playing.discard(slot)
                self._away_since[slot] = self.clock()
            case ev.ConnectionChanged(state=ev.ConnectionState.CONNECTED):
                self._playing.clear()
                self._away_since.clear()

    def _away(self, slot: int) -> bool:
        """Not playing and not done: someone who could use a notification."""
        if self.state.statuses.get(slot) == ClientStatus.GOAL or slot in self._playing:
            return False
        if slot in self._away_since:
            return True
        if self.state.is_online(slot):  # joined while the bot wasn't watching
            self._notified.discard(slot)
            return False
        return True

    def release(self, held: Held) -> list[ev.ItemSent]:
        """Pop the held items to notify now; drop those of players who came back or were already notified."""
        due: list[ev.ItemSent] = []
        for slot in list(held):
            if not self._away(slot) or slot in self._notified:
                del held[slot]
            elif self.clock() - self._away_since.get(slot, -math.inf) >= self.grace:
                due += held.pop(slot)
                self._notified.add(slot)
        return due
