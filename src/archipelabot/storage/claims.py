from enum import StrEnum

from .db import Database


class NotifMode(StrEnum):
    THREAD = "thread"
    DM = "dm"
    OFF = "off"


class ClaimRepo:
    def __init__(self, db: Database) -> None:
        self.db = db

    async def for_room(self, room_id: int) -> dict[int, int]:
        async with self.db.conn.execute("SELECT slot, user_id FROM claim WHERE room_id = ?", (room_id,)) as cur:
            return {row["slot"]: row["user_id"] for row in await cur.fetchall()}

    async def set(self, room_id: int, slot: int, slot_name: str, user_id: int) -> None:
        await self.db.conn.execute(
            """
            INSERT INTO claim (room_id, slot, slot_name, user_id) VALUES (?, ?, ?, ?)
            ON CONFLICT (room_id, slot) DO UPDATE
                SET user_id = excluded.user_id, slot_name = excluded.slot_name, claimed_at = datetime('now')
            """,
            (room_id, slot, slot_name, user_id),
        )
        await self.db.conn.commit()

    async def remove(self, room_id: int, slot: int) -> None:
        await self.db.conn.execute("DELETE FROM claim WHERE room_id = ? AND slot = ?", (room_id, slot))
        await self.db.conn.commit()

    async def previous(self, guild_id: int, slot_names: list[str]) -> dict[str, int]:
        """Most recent owner of each slot name in this guild's earlier rooms."""
        if not slot_names:
            return {}
        placeholders = ", ".join("?" * len(slot_names))
        async with self.db.conn.execute(
            f"""
            SELECT claim.slot_name, claim.user_id FROM claim JOIN room ON room.id = claim.room_id
            WHERE room.guild_id = ? AND claim.slot_name IN ({placeholders})
            ORDER BY claim.claimed_at, claim.rowid
            """,
            (guild_id, *slot_names),
        ) as cur:
            return {row["slot_name"]: row["user_id"] for row in await cur.fetchall()}


class NotifPrefs:
    def __init__(self, db: Database) -> None:
        self.db = db
        self._modes: dict[int, NotifMode] = {}

    async def load(self) -> None:
        async with self.db.conn.execute("SELECT user_id, notif_mode FROM user_pref") as cur:
            self._modes = {row["user_id"]: NotifMode(row["notif_mode"]) for row in await cur.fetchall()}

    def mode(self, user_id: int) -> NotifMode:
        return self._modes.get(user_id, NotifMode.THREAD)

    async def set(self, user_id: int, mode: NotifMode) -> None:
        await self.db.conn.execute(
            """
            INSERT INTO user_pref (user_id, notif_mode) VALUES (?, ?)
            ON CONFLICT (user_id) DO UPDATE SET notif_mode = excluded.notif_mode
            """,
            (user_id, mode.value),
        )
        await self.db.conn.commit()
        self._modes[user_id] = mode
