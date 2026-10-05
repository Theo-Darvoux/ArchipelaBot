from dataclasses import dataclass

from .db import Database


@dataclass(slots=True)
class GuildConfig:
    guild_id: int
    forum_id: int | None = None
    recap_channel_id: int | None = None


class GuildRepo:
    def __init__(self, db: Database) -> None:
        self.db = db

    async def get(self, guild_id: int) -> GuildConfig:
        async with self.db.conn.execute(
            "SELECT forum_id, recap_channel_id FROM guild_config WHERE guild_id = ?", (guild_id,)
        ) as cur:
            row = await cur.fetchone()
        if row is None:
            return GuildConfig(guild_id)
        return GuildConfig(guild_id, row["forum_id"], row["recap_channel_id"])

    async def save(self, config: GuildConfig) -> None:
        await self.db.conn.execute(
            """
            INSERT INTO guild_config (guild_id, forum_id, recap_channel_id) VALUES (?, ?, ?)
            ON CONFLICT (guild_id) DO UPDATE
                SET forum_id = excluded.forum_id, recap_channel_id = excluded.recap_channel_id
            """,
            (config.guild_id, config.forum_id, config.recap_channel_id),
        )
        await self.db.conn.commit()
