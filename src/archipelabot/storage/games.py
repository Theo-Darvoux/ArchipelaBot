import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Literal

from ..core.yamls import YamlError, YamlFile, parse_players
from .db import Database
from .rooms import _parse_utc

log = logging.getLogger(__name__)

type GameStatus = Literal["open", "started", "cancelled"]


@dataclass(slots=True)
class GameRecord:
    guild_id: int
    name: str
    created_by: int
    description: str = ""
    starts_at: datetime | None = None
    thread_id: int | None = None
    panel_message_id: int | None = None
    status: GameStatus = "open"
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC).replace(microsecond=0))
    id: int | None = None


class GameRepo:
    def __init__(self, db: Database) -> None:
        self.db = db

    async def create(self, game: GameRecord) -> GameRecord:
        cur = await self.db.conn.execute(
            """
            INSERT INTO game (guild_id, thread_id, panel_message_id, name, description, starts_at, status, created_by,
                              created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                game.guild_id, game.thread_id, game.panel_message_id, game.name, game.description,
                game.starts_at and game.starts_at.isoformat(), game.status, game.created_by,
                game.created_at.isoformat(),
            ),
        )  # fmt: skip
        await self.db.conn.commit()
        game.id = cur.lastrowid
        return game

    async def open(self) -> list[GameRecord]:
        return await self._select("WHERE status = 'open' ORDER BY id")

    async def started_in(self, thread_id: int) -> GameRecord | None:
        rows = await self._select("WHERE status = 'started' AND thread_id = ?", thread_id)
        return rows[0] if rows else None

    async def _select(self, where: str, *params: object) -> list[GameRecord]:
        async with self.db.conn.execute(f"SELECT * FROM game {where}", params) as cur:
            rows = await cur.fetchall()
        return [
            GameRecord(
                id=row["id"],
                guild_id=row["guild_id"],
                thread_id=row["thread_id"],
                panel_message_id=row["panel_message_id"],
                name=row["name"],
                description=row["description"],
                starts_at=_parse_utc(row["starts_at"]) if row["starts_at"] else None,
                status=row["status"],
                created_by=row["created_by"],
                created_at=_parse_utc(row["created_at"]),
            )
            for row in rows
        ]

    async def save_post(self, game: GameRecord) -> None:
        await self.db.conn.execute(
            "UPDATE game SET thread_id = ?, panel_message_id = ? WHERE id = ?",
            (game.thread_id, game.panel_message_id, game.id),
        )
        await self.db.conn.commit()

    async def save_details(self, game: GameRecord) -> None:
        await self.db.conn.execute(
            "UPDATE game SET name = ?, description = ?, starts_at = ? WHERE id = ?",
            (game.name, game.description, game.starts_at and game.starts_at.isoformat(), game.id),
        )
        await self.db.conn.commit()

    async def set_status(self, game: GameRecord, status: GameStatus) -> None:
        game.status = status
        await self.db.conn.execute("UPDATE game SET status = ? WHERE id = ?", (status, game.id))
        await self.db.conn.commit()

    async def yamls(self, game_id: int) -> list[YamlFile]:
        async with self.db.conn.execute(
            "SELECT id, user_id, filename, content FROM game_yaml WHERE game_id = ? ORDER BY id", (game_id,)
        ) as cur:
            rows = await cur.fetchall()
        files = []
        for row in rows:
            try:
                players = parse_players(row["content"])
            except YamlError:
                log.warning("Stored yaml %s can no longer be read", row["id"], exc_info=True)
                players = []
            files.append(YamlFile(row["user_id"], row["filename"], row["content"], players, id=row["id"]))
        return files

    async def replace_yamls(self, game_id: int, new: YamlFile, replaced: list[YamlFile]) -> None:
        conn = self.db.conn
        await conn.executemany("DELETE FROM game_yaml WHERE id = ?", [(old.id,) for old in replaced])
        cur = await conn.execute(
            "INSERT INTO game_yaml (game_id, user_id, filename, content) VALUES (?, ?, ?, ?)",
            (game_id, new.user_id, new.filename, new.content),
        )
        await conn.commit()
        new.id = cur.lastrowid

    async def remove_yaml(self, yaml_id: int) -> None:
        await self.db.conn.execute("DELETE FROM game_yaml WHERE id = ?", (yaml_id,))
        await self.db.conn.commit()
