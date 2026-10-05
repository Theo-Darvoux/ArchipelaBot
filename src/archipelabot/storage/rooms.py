from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel

from ..ap.protocol import ItemFlags
from ..ap.webhost import WebhostRoom
from .db import Database

type RoomStatus = Literal["active", "finished", "stopped"]


class RoomSettings(BaseModel):
    show_progression: bool = True
    show_useful: bool = True
    show_filler: bool = False
    show_traps: bool = True
    show_joins: bool = False
    show_deaths: bool = True
    show_hints: bool = True
    chat_bridge: bool = True

    def shows_item(self, flags: ItemFlags) -> bool:
        if flags & ItemFlags.PROGRESSION:
            return self.show_progression
        if flags & ItemFlags.USEFUL:
            return self.show_useful
        if flags & ItemFlags.TRAP:
            return self.show_traps
        return self.show_filler


@dataclass(slots=True)
class RoomRecord:
    guild_id: int
    name: str
    address: str
    slot: str
    created_by: int
    password: str | None = None
    webhost: WebhostRoom | None = None
    thread_id: int | None = None
    panel_message_id: int | None = None
    settings: RoomSettings = field(default_factory=RoomSettings)
    status: RoomStatus = "active"
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC).replace(microsecond=0))
    ended_at: datetime | None = None
    id: int | None = None


class RoomRepo:
    def __init__(self, db: Database) -> None:
        self.db = db

    async def create(self, room: RoomRecord) -> RoomRecord:
        cur = await self.db.conn.execute(
            """
            INSERT INTO room (guild_id, thread_id, panel_message_id, name, address, webhost_base, webhost_room,
                              slot, password, settings, status, created_by, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                room.guild_id, room.thread_id, room.panel_message_id, room.name, room.address,
                room.webhost and room.webhost.base_url, room.webhost and room.webhost.room_id,
                room.slot, room.password, room.settings.model_dump_json(), room.status, room.created_by,
                room.created_at.isoformat(),
            ),
        )  # fmt: skip
        await self.db.conn.commit()
        room.id = cur.lastrowid
        return room

    async def get(self, room_id: int) -> RoomRecord | None:
        rows = await self._select("WHERE id = ?", room_id)
        return rows[0] if rows else None

    async def by_thread(self, thread_id: int) -> RoomRecord | None:
        rows = await self._select("WHERE thread_id = ?", thread_id)
        return rows[0] if rows else None

    async def active(self) -> list[RoomRecord]:
        return await self._select("WHERE status = 'active' ORDER BY id")

    async def save_settings(self, room: RoomRecord) -> None:
        await self._update(room, "settings = ?", room.settings.model_dump_json())

    async def save_address(self, room: RoomRecord) -> None:
        await self._update(room, "address = ?", room.address)

    async def save_password(self, room: RoomRecord) -> None:
        await self._update(room, "password = ?", room.password)

    async def set_status(self, room: RoomRecord, status: RoomStatus) -> None:
        room.status = status
        room.ended_at = datetime.now(UTC).replace(microsecond=0) if status != "active" else None
        await self._update(room, "status = ?, ended_at = ?", status, room.ended_at and room.ended_at.isoformat())

    async def _update(self, room: RoomRecord, assignments: str, *values: object) -> None:
        await self.db.conn.execute(f"UPDATE room SET {assignments} WHERE id = ?", (*values, room.id))
        await self.db.conn.commit()

    async def _select(self, where: str, *params: object) -> list[RoomRecord]:
        async with self.db.conn.execute(f"SELECT * FROM room {where}", params) as cur:
            rows = await cur.fetchall()
        return [
            RoomRecord(
                id=row["id"],
                guild_id=row["guild_id"],
                thread_id=row["thread_id"],
                panel_message_id=row["panel_message_id"],
                name=row["name"],
                address=row["address"],
                webhost=WebhostRoom(row["webhost_base"], row["webhost_room"]) if row["webhost_room"] else None,
                slot=row["slot"],
                password=row["password"],
                settings=RoomSettings.model_validate_json(row["settings"]),
                status=row["status"],
                created_by=row["created_by"],
                created_at=_parse_utc(row["created_at"]),
                ended_at=_parse_utc(row["ended_at"]) if row["ended_at"] else None,
            )
            for row in rows
        ]


def _parse_utc(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
