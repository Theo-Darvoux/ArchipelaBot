import asyncio
import logging
from datetime import UTC, datetime
from pathlib import Path

import aiosqlite

log = logging.getLogger(__name__)

# Each entry is applied once, in order; PRAGMA user_version tracks the last one applied.
# Never edit a migration that has shipped: append a new one instead.
MIGRATIONS: list[str] = [
    """
    CREATE TABLE guild_config (
        guild_id        INTEGER PRIMARY KEY,
        forum_id        INTEGER,
        recap_channel_id INTEGER
    );
    CREATE TABLE user_pref (
        user_id     INTEGER PRIMARY KEY,
        notif_mode  TEXT NOT NULL DEFAULT 'thread' CHECK (notif_mode IN ('thread', 'dm', 'off'))
    );
    """,
    """
    CREATE TABLE room (
        id                  INTEGER PRIMARY KEY,
        guild_id            INTEGER NOT NULL,
        thread_id           INTEGER UNIQUE,
        panel_message_id    INTEGER,
        name                TEXT NOT NULL,
        address             TEXT NOT NULL,      -- last known host:port
        webhost_base        TEXT,               -- e.g. https://archipelago.gg, when tracked from a room URL
        webhost_room        TEXT,
        slot                TEXT NOT NULL,      -- slot the bot connects with (TextOnly)
        password            TEXT,
        settings            TEXT NOT NULL DEFAULT '{}',
        status              TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'finished', 'stopped')),
        created_by          INTEGER NOT NULL,
        created_at          TEXT NOT NULL DEFAULT (datetime('now')),
        ended_at            TEXT
    );
    CREATE INDEX room_status ON room (status);
    """,
    """
    CREATE TABLE claim (
        room_id     INTEGER NOT NULL REFERENCES room (id) ON DELETE CASCADE,
        slot        INTEGER NOT NULL,
        slot_name   TEXT NOT NULL,      -- to recognise players in later rooms
        user_id     INTEGER NOT NULL,
        claimed_at  TEXT NOT NULL DEFAULT (datetime('now')),
        PRIMARY KEY (room_id, slot)
    );
    CREATE INDEX claim_slot_name ON claim (slot_name);
    """,
    """
    CREATE TABLE room_event (
        id          INTEGER PRIMARY KEY,
        room_id     INTEGER NOT NULL REFERENCES room (id) ON DELETE CASCADE,
        at          TEXT NOT NULL,
        kind        TEXT NOT NULL,      -- goal, death, release, collect
        slot        INTEGER,
        data        TEXT NOT NULL DEFAULT '{}'
    );
    CREATE INDEX room_event_room ON room_event (room_id, kind);
    CREATE TABLE progress_snapshot (
        room_id     INTEGER NOT NULL REFERENCES room (id) ON DELETE CASCADE,
        at          TEXT NOT NULL,
        slot        INTEGER NOT NULL,
        checked     INTEGER NOT NULL,
        total       INTEGER
    );
    CREATE INDEX progress_snapshot_room ON progress_snapshot (room_id, at);
    """,
    """
    ALTER TABLE room ADD COLUMN hints_message_id INTEGER;
    """,
    """
    ALTER TABLE room DROP COLUMN hints_message_id;
    CREATE TABLE room_slot (
        room_id     INTEGER NOT NULL REFERENCES room (id) ON DELETE CASCADE,
        slot        INTEGER NOT NULL,
        name        TEXT NOT NULL,
        game        TEXT NOT NULL,
        alias       TEXT NOT NULL DEFAULT '',
        is_group    INTEGER NOT NULL DEFAULT 0,
        total       INTEGER,
        goal        INTEGER NOT NULL DEFAULT 0,
        PRIMARY KEY (room_id, slot)
    );
    """,
]


class Database:
    def __init__(self, conn: aiosqlite.Connection) -> None:
        self.conn = conn

    @classmethod
    async def open(cls, path: Path | str) -> "Database":
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        conn = await aiosqlite.connect(path)
        conn.row_factory = aiosqlite.Row
        await conn.execute("PRAGMA foreign_keys = ON")
        await conn.execute("PRAGMA journal_mode = WAL")
        db = cls(conn)
        await db.migrate()
        return db

    async def migrate(self) -> None:
        async with self.conn.execute("PRAGMA user_version") as cur:
            (version,) = await cur.fetchone()
        for i, script in enumerate(MIGRATIONS[version:], start=version + 1):
            log.info("Applying database migration %d", i)
            await self.conn.executescript(f"BEGIN;\n{script}\nPRAGMA user_version = {i};\nCOMMIT;")

    async def backup(self, directory: Path, keep: int) -> Path:
        """Today's copy of the database, keeping the `keep` most recent ones."""
        await asyncio.to_thread(directory.mkdir, parents=True, exist_ok=True)
        target = directory / f"archipelabot-{datetime.now(UTC):%Y-%m-%d}.db"
        async with aiosqlite.connect(target) as copy:
            await self.conn.backup(copy)
        await asyncio.to_thread(_prune, directory, keep)
        return target

    async def close(self) -> None:
        await self.conn.close()


def _prune(directory: Path, keep: int) -> None:
    for old in sorted(directory.glob("archipelabot-*.db"))[:-keep]:
        old.unlink()
