from dataclasses import dataclass

from .db import Database


@dataclass(frozen=True, slots=True)
class ItemPing:
    user_id: int
    slot: int
    item: str


class PingRepo:
    def __init__(self, db: Database) -> None:
        self.db = db

    async def for_room(self, room_id: int) -> set[ItemPing]:
        query = "SELECT user_id, slot, item FROM item_ping WHERE room_id = ?"
        async with self.db.conn.execute(query, (room_id,)) as cur:
            return {ItemPing(r["user_id"], r["slot"], r["item"]) for r in await cur.fetchall()}

    async def add(self, room_id: int, ping: ItemPing) -> None:
        await self.db.conn.execute(
            "INSERT OR IGNORE INTO item_ping (room_id, user_id, slot, item) VALUES (?, ?, ?, ?)",
            (room_id, ping.user_id, ping.slot, ping.item),
        )
        await self.db.conn.commit()

    async def remove(self, room_id: int, ping: ItemPing) -> None:
        await self.db.conn.execute(
            "DELETE FROM item_ping WHERE room_id = ? AND user_id = ? AND slot = ? AND item = ?",
            (room_id, ping.user_id, ping.slot, ping.item),
        )
        await self.db.conn.commit()
