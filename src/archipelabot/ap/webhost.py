"""archipelago.gg (or any Archipelago WebHost): room lookup by URL, current port.

The bot never wakes a sleeping room on its own: a room only restarts when someone opens its page.
`wake` exists for the one case where a user explicitly asks to track a room.
"""

import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.parse import urlsplit

import aiohttp

ROOM_URL = re.compile(r"^(?:(?P<scheme>https?)://)?(?P<host>[\w.-]+(?::\d+)?)/room/(?P<room>[\w-]+)/?$")
USER_AGENT = "ArchipelaBot (Discord bot)"


class WebhostError(Exception):
    pass


@dataclass(frozen=True, slots=True)
class WebhostRoom:
    base_url: str
    room_id: str

    @property
    def host(self) -> str:
        return urlsplit(self.base_url).hostname or ""

    @property
    def page_url(self) -> str:
        return f"{self.base_url}/room/{self.room_id}"

    def address(self, port: int) -> str:
        return f"{self.host}:{port}"


@dataclass(frozen=True, slots=True)
class RoomStatus:
    last_port: int | None
    players: list[tuple[str, str]]
    last_activity: datetime
    timeout: int
    tracker: str | None

    def is_open(self, now: datetime | None = None) -> bool:
        now = now or datetime.now(UTC)
        return bool(self.last_port) and now - self.last_activity < timedelta(seconds=self.timeout)

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> "RoomStatus":
        return cls(
            last_port=data.get("last_port") or None,
            players=[(name, game) for name, game in data.get("players", [])],
            last_activity=parse_http_date(data["last_activity"]),
            timeout=int(data.get("timeout", 0)),
            tracker=data.get("tracker"),
        )


def parse_http_date(value: str) -> datetime:
    try:
        parsed = parsedate_to_datetime(value)  # Flask's default: "Sat, 04 Oct 2026 20:00:00 GMT"
    except (TypeError, ValueError):
        parsed = datetime.fromisoformat(value)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def parse_room_url(text: str) -> WebhostRoom | None:
    match = ROOM_URL.match(text.strip())
    if not match:
        return None
    scheme = match["scheme"] or "https"
    return WebhostRoom(f"{scheme}://{match['host']}", match["room"])


class WebhostClient:
    def __init__(self, session: aiohttp.ClientSession) -> None:
        self.session = session

    async def _get_json(self, url: str) -> Any:
        try:
            async with self.session.get(url, headers={"User-Agent": USER_AGENT}) as response:
                if response.status == 404:
                    raise WebhostError("introuvable")
                response.raise_for_status()
                return await response.json()
        except (aiohttp.ClientError, TimeoutError, ValueError) as e:
            raise WebhostError(str(e) or type(e).__name__) from e

    async def room_status(self, room: WebhostRoom) -> RoomStatus:
        try:
            return RoomStatus.from_json(await self._get_json(f"{room.base_url}/api/room_status/{room.room_id}"))
        except WebhostError as e:
            raise WebhostError("room introuvable" if str(e) == "introuvable" else str(e)) from e
        except (KeyError, ValueError, TypeError) as e:
            raise WebhostError(f"réponse inattendue ({e})") from e

    # The WebHost caches these for 60 s (tracker) and 300 s (static tracker): no point asking more often.
    async def tracker(self, room: WebhostRoom, tracker_id: str) -> dict[str, Any]:
        return await self._get_json(f"{room.base_url}/api/tracker/{tracker_id}")

    async def static_tracker(self, room: WebhostRoom, tracker_id: str) -> dict[str, Any]:
        return await self._get_json(f"{room.base_url}/api/static_tracker/{tracker_id}")

    async def wake(self, room: WebhostRoom) -> None:
        """Open the room page, which makes the WebHost start the room if it was asleep."""
        try:
            async with self.session.get(room.page_url, headers={"User-Agent": USER_AGENT}) as response:
                response.raise_for_status()
        except (aiohttp.ClientError, TimeoutError) as e:
            raise WebhostError(str(e) or type(e).__name__) from e

    async def current_address(self, room: WebhostRoom) -> str | None:
        """Address of the running room, or None while it's asleep."""
        status = await self.room_status(room)
        return room.address(status.last_port) if status.is_open() and status.last_port else None
