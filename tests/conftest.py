import pytest

from archipelabot.bot import ArchipelaBot
from archipelabot.config import Settings
from archipelabot.storage.db import Database

from .ap_server import ap_server  # noqa: F401


@pytest.fixture
async def db():
    database = await Database.open(":memory:")
    yield database
    await database.close()


@pytest.fixture
def settings() -> Settings:
    return Settings(discord_token="test-token", dev_guild_id=None, _env_file=None)


@pytest.fixture
async def bot(settings, db):
    """A bot with every extension loaded, never logged in to Discord."""
    instance = ArchipelaBot(settings, db)
    await instance.load_extensions()
    yield instance
    await instance.close()
