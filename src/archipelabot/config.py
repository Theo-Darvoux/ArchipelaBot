from pathlib import Path

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", env_ignore_empty=True, extra="ignore")

    discord_token: SecretStr
    dev_guild_id: int | None = None
    database_path: Path = Path("data/archipelabot.db")
    log_level: str = "INFO"
    timezone: str = "Europe/Paris"
    # an Archipelago source folder, or a folder of them for several versions, to compute who is blocked
    archipelago_path: Path | None = None
    archipelago_python: Path | None = None  # for a single folder without its .venv
    # the address players use to reach the bot's machine: games hosted by the bot are only possible with it
    host_address: str | None = None
    host_ports: str = "38281-38300"
