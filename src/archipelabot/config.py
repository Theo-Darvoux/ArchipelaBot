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
