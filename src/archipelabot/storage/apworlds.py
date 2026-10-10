from collections.abc import Iterable
from typing import Any

from ..core.apworlds import Apworld, Attached
from .db import Database
from .rooms import _parse_utc

COLUMNS = "a.id, a.game, a.module, a.version, a.sha256, a.uploaded_by, a.uploaded_at"


class ApworldRepo:
    """Every apworld sent on a server is kept there, to be picked again in its later games."""

    def __init__(self, db: Database) -> None:
        self.db = db

    async def add(self, guild_id: int, apworld: Apworld, content: bytes) -> Apworld:
        await self.db.conn.execute(
            """
            INSERT INTO apworld (guild_id, game, module, version, sha256, uploaded_by, content)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (guild_id, sha256) DO NOTHING
            """,
            (guild_id, apworld.game, apworld.module, apworld.version, apworld.sha256, apworld.uploaded_by, content),
        )
        await self.db.conn.commit()
        [stored] = await self._select("WHERE a.guild_id = ? AND a.sha256 = ?", guild_id, apworld.sha256)
        return stored

    async def get(self, guild_id: int, apworld_id: int) -> Apworld | None:
        rows = await self._select("WHERE a.guild_id = ? AND a.id = ?", guild_id, apworld_id)
        return rows[0] if rows else None

    async def library(self, guild_id: int, games: Iterable[str]) -> list[Apworld]:
        """This server's apworlds for these games, newest first."""
        games = list(games)
        if not games:
            return []
        marks = ", ".join("?" * len(games))
        return await self._select(f"WHERE a.guild_id = ? AND a.game IN ({marks}) ORDER BY a.id DESC", guild_id, *games)

    async def attached(self, game_id: int) -> list[Attached]:
        async with self.db.conn.execute(
            f"""
            SELECT {COLUMNS}, l.user_id FROM game_apworld l JOIN apworld a ON a.id = l.apworld_id
            WHERE l.game_id = ? ORDER BY a.game
            """,
            (game_id,),
        ) as cur:
            rows = await cur.fetchall()
        return [Attached(_apworld(row), row["user_id"]) for row in rows]

    async def attach(self, game_id: int, apworld: Apworld, user_id: int) -> list[Attached]:
        """Use this apworld in the game, instead of another one for the same world; returns the ones it replaces."""
        replaced = [
            a for a in await self.attached(game_id) if a.apworld.game == apworld.game and a.apworld.id != apworld.id
        ]
        conn = self.db.conn
        await conn.executemany(
            "DELETE FROM game_apworld WHERE game_id = ? AND apworld_id = ?",
            [(game_id, a.apworld.id) for a in replaced],
        )
        await conn.execute(
            "INSERT OR REPLACE INTO game_apworld (game_id, apworld_id, user_id) VALUES (?, ?, ?)",
            (game_id, apworld.id, user_id),
        )
        await conn.commit()
        return replaced

    async def detach(self, game_id: int, apworld_id: int) -> None:
        await self.db.conn.execute(
            "DELETE FROM game_apworld WHERE game_id = ? AND apworld_id = ?", (game_id, apworld_id)
        )
        await self.db.conn.commit()

    async def contents(self, ids: Iterable[int]) -> dict[int, bytes]:
        ids = list(ids)
        if not ids:
            return {}
        marks = ", ".join("?" * len(ids))
        async with self.db.conn.execute(f"SELECT id, content FROM apworld WHERE id IN ({marks})", ids) as cur:
            return {row["id"]: row["content"] for row in await cur.fetchall()}

    async def _select(self, where: str, *params: object) -> list[Apworld]:
        async with self.db.conn.execute(f"SELECT {COLUMNS} FROM apworld a {where}", params) as cur:
            return [_apworld(row) for row in await cur.fetchall()]


def _apworld(row: Any) -> Apworld:
    return Apworld(
        game=row["game"],
        module=row["module"],
        version=row["version"],
        sha256=row["sha256"],
        uploaded_by=row["uploaded_by"],
        uploaded_at=_parse_utc(row["uploaded_at"]),
        id=row["id"],
    )
