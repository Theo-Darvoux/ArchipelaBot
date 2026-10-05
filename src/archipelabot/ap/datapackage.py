"""Item and location names, cached on disk by data package checksum."""

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class GameData:
    checksum: str
    items: dict[int, str]
    locations: dict[int, str]

    @classmethod
    def from_wire(cls, raw: dict[str, Any]) -> "GameData":
        return cls(
            checksum=raw.get("checksum", ""),
            items={v: k for k, v in raw["item_name_to_id"].items()},
            locations={v: k for k, v in raw["location_name_to_id"].items()},
        )


class DataPackageStore:
    """Shared by every room: games are loaded once, from disk when possible."""

    def __init__(self, cache_dir: Path | None) -> None:
        self.cache_dir = cache_dir
        self._games: dict[str, GameData] = {}

    def _path(self, game: str, checksum: str) -> Path | None:
        if self.cache_dir is None or not checksum:
            return None
        safe = "".join(c if c.isalnum() else "_" for c in game)
        return self.cache_dir / f"{safe}-{checksum}.json"

    def missing(self, checksums: dict[str, str]) -> list[str]:
        """Games whose data isn't loaded at the right checksum, after trying the disk cache."""
        missing = []
        for game, checksum in checksums.items():
            if (loaded := self._games.get(game)) and loaded.checksum == checksum:
                continue
            path = self._path(game, checksum)
            if path and path.exists():
                try:
                    self._games[game] = GameData.from_wire(json.loads(path.read_text("utf-8")))
                    continue
                except (ValueError, KeyError):
                    log.warning("Corrupted data package cache %s, refetching", path)
            missing.append(game)
        return missing

    def add(self, games: dict[str, dict[str, Any]]) -> None:
        for game, raw in games.items():
            self._games[game] = GameData.from_wire(raw)
            if path := self._path(game, raw.get("checksum", "")):
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(json.dumps(raw), "utf-8")

    def item_name(self, game: str, item_id: int) -> str:
        data = self._games.get(game)
        return (data and data.items.get(item_id)) or f"Item #{item_id}"

    def location_name(self, game: str, location_id: int) -> str:
        data = self._games.get(game)
        return (data and data.locations.get(location_id)) or f"Lieu #{location_id}"
