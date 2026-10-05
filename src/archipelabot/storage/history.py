import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal

from .db import Database

type EventKind = Literal["goal", "death", "release", "collect", "hint"]


@dataclass(frozen=True, slots=True)
class LoggedEvent:
    at: datetime
    kind: EventKind
    slot: int | None
    data: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class SavedSlot:
    slot: int
    name: str
    game: str
    alias: str = ""
    is_group: bool = False
    total: int | None = None
    goal: bool = False


@dataclass(frozen=True, slots=True)
class Snapshot:
    at: datetime
    slot: int
    checked: int
    total: int | None


def _utc(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


class HistoryRepo:
    def __init__(self, db: Database) -> None:
        self.db = db

    async def log(self, room_id: int, event: LoggedEvent) -> None:
        await self.db.conn.execute(
            "INSERT INTO room_event (room_id, at, kind, slot, data) VALUES (?, ?, ?, ?, ?)",
            (room_id, event.at.isoformat(), event.kind, event.slot, json.dumps(event.data)),
        )
        await self.db.conn.commit()

    async def events(self, room_id: int) -> list[LoggedEvent]:
        async with self.db.conn.execute(
            "SELECT at, kind, slot, data FROM room_event WHERE room_id = ? ORDER BY at, id", (room_id,)
        ) as cur:
            rows = await cur.fetchall()
        return [LoggedEvent(_utc(r["at"]), r["kind"], r["slot"], json.loads(r["data"])) for r in rows]

    async def snapshot(self, room_id: int, snapshots: list[Snapshot]) -> None:
        await self.db.conn.executemany(
            "INSERT INTO progress_snapshot (room_id, at, slot, checked, total) VALUES (?, ?, ?, ?, ?)",
            [(room_id, s.at.isoformat(), s.slot, s.checked, s.total) for s in snapshots],
        )
        await self.db.conn.commit()

    async def save_slots(self, room_id: int, slots: list[SavedSlot]) -> None:
        await self.db.conn.executemany(
            """
            INSERT INTO room_slot (room_id, slot, name, game, alias, is_group, total, goal)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (room_id, slot) DO UPDATE SET
                name = excluded.name, game = excluded.game, alias = excluded.alias, is_group = excluded.is_group,
                total = coalesce(excluded.total, total), goal = excluded.goal
            """,
            [(room_id, s.slot, s.name, s.game, s.alias, s.is_group, s.total, s.goal) for s in slots],
        )
        await self.db.conn.commit()

    async def slots(self, room_id: int) -> list[SavedSlot]:
        async with self.db.conn.execute(
            "SELECT slot, name, game, alias, is_group, total, goal FROM room_slot WHERE room_id = ? ORDER BY slot",
            (room_id,),
        ) as cur:
            rows = await cur.fetchall()
        return [
            SavedSlot(r["slot"], r["name"], r["game"], r["alias"], bool(r["is_group"]), r["total"], bool(r["goal"]))
            for r in rows
        ]

    async def snapshots(self, room_id: int) -> list[Snapshot]:
        async with self.db.conn.execute(
            "SELECT at, slot, checked, total FROM progress_snapshot WHERE room_id = ? ORDER BY at, slot", (room_id,)
        ) as cur:
            rows = await cur.fetchall()
        return [Snapshot(_utc(r["at"]), r["slot"], r["checked"], r["total"]) for r in rows]
