from pathlib import Path

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    discord_token: SecretStr
    dev_guild_id: int | None = None
    database_path: Path = Path("data/archipelabot.db")
    log_level: str = "INFO"
    timezone: str = "Europe/Paris"
