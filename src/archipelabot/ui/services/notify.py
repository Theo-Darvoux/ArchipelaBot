"""Sends received progression items by DM, grouped per player, once per absence from the game."""

import asyncio
import logging
from collections import defaultdict
from collections.abc import Callable
from typing import Protocol

from discord import ui

from ...core import events as ev
from ...core.room import RoomState
from ..render.feed import Pinger, pinged_user
from ..render.notify import received_items_view
from .presence import Held, Presence

log = logging.getLogger(__name__)


class DMSender(Protocol):
    async def send_dm(self, user_id: int, view: ui.LayoutView) -> None: ...


class NotifyService:
    def __init__(
        self,
        state: RoomState,
        room_name: Callable[[], str],
        jump_url: Callable[[], str | None],
        dm_target: Pinger,
        sender: DMSender,
        *,
        presence: Presence | None = None,
        interval: float = 3.0,
    ) -> None:
        self.state = state
        self.room_name = room_name
        self.jump_url = jump_url
        self.dm_target = dm_target
        self.sender = sender
        self.interval = interval
        self.presence = presence or Presence(state)
        self._held: Held = defaultdict(list)

    async def handle(self, event: ev.Event) -> None:
        match event:
            case ev.ItemSent():
                items: tuple[ev.ItemSent, ...] = (event,)
            case ev.Released(items=items):
                pass
            case _:
                return
        for item in items:
            if pinged_user(item, self.dm_target):
                self._held[item.receiver].append(item)

    async def flush(self) -> None:
        by_user: dict[int, list[ev.ItemSent]] = defaultdict(list)
        for item in self.presence.release(self._held):
            if (user := self.dm_target(item.receiver)) is not None:
                by_user[user].append(item)
        for user, items in by_user.items():
            view = received_items_view(self.room_name(), items, self.state, self.jump_url())
            try:
                await self.sender.send_dm(user, view)
            except Exception:
                log.info("Could not DM user %s (DMs closed?)", user, exc_info=True)

    async def run(self) -> None:
        while True:
            await asyncio.sleep(self.interval)
            try:
                await self.flush()
            except Exception:
                log.exception("Could not send the notifications of %s", self.state.address)
