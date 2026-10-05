import asyncio
import contextlib
import logging
import signal

import discord
from pydantic import ValidationError

from .bot import ArchipelaBot
from .config import Settings
from .storage.db import Database


async def run(settings: Settings) -> None:
    db = await Database.open(settings.database_path)
    try:
        async with ArchipelaBot(settings, db) as bot:
            # Docker stops the bot with SIGTERM: leave like on Ctrl+C, so that pending posts are saved.
            start = asyncio.ensure_future(bot.start(settings.discord_token.get_secret_value()))
            asyncio.get_running_loop().add_signal_handler(signal.SIGTERM, start.cancel)
            with contextlib.suppress(asyncio.CancelledError):
                await start
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
