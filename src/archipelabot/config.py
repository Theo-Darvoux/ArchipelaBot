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
    archipelago_path: Path | None = None  # an Archipelago source folder, to compute who is blocked
    archipelago_python: Path | None = None

    @property
    def logic_python(self) -> Path | None:
        if self.archipelago_path is None:
            return None
        return self.archipelago_python or self.archipelago_path / ".venv" / "bin" / "python"
