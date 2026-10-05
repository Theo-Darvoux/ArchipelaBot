import asyncio
import logging

import discord
from pydantic import ValidationError

from .bot import ArchipelaBot
from .config import Settings
from .storage.db import Database


async def run(settings: Settings) -> None:
    db = await Database.open(settings.database_path)
    try:
        async with ArchipelaBot(settings, db) as bot:
            await bot.start(settings.discord_token.get_secret_value())
    finally:
        await db.close()


def main() -> None:
    try:
        settings = Settings()
    except ValidationError as e:
        missing = ", ".join(str(err["loc"][0]).upper() for err in e.errors())
        raise SystemExit(f"Configuration invalide ou manquante : {missing}.") from None
    discord.utils.setup_logging(level=logging.getLevelNamesMapping()[settings.log_level.upper()])
    asyncio.run(run(settings))
