"""Item and location names, cached on disk by data package checksum."""

import asyncio
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
    """Shared by every room: each version of a game is loaded once, from disk when possible."""

    def __init__(self, cache_dir: Path | None) -> None:
        self.cache_dir = cache_dir
        self._games: dict[tuple[str, str], GameData] = {}
        self._latest: dict[str, GameData] = {}

    def _path(self, game: str, checksum: str) -> Path | None:
        if self.cache_dir is None or not checksum:
            return None
        safe = "".join(c if c.isalnum() else "_" for c in game)
        return self.cache_dir / f"{safe}-{checksum}.json"

    def _store(self, game: str, data: GameData) -> None:
        self._games[game, data.checksum] = self._latest[game] = data

    async def missing(self, checksums: dict[str, str]) -> list[str]:
        """Games whose data isn't loaded at the right checksum, after trying the disk cache."""
        missing = []
        for game, checksum in checksums.items():
            if (game, checksum) in self._games:
                continue
            path = self._path(game, checksum)
            if path and path.exists():
                try:
                    self._store(game, await asyncio.to_thread(_load, path))
                    continue
                except (ValueError, KeyError):
                    log.warning("Corrupted data package cache %s, refetching", path)
            missing.append(game)
        return missing

    async def add(self, games: dict[str, dict[str, Any]]) -> None:
        for game, raw in games.items():
            self._store(game, GameData.from_wire(raw))
            if path := self._path(game, raw.get("checksum", "")):
                await asyncio.to_thread(_save, path, raw)

    def _data(self, game: str, checksum: str) -> GameData | None:
        return self._games.get((game, checksum)) or self._latest.get(game)

    def item_name(self, game: str, checksum: str, item_id: int) -> str:
        data = self._data(game, checksum)
        return (data and data.items.get(item_id)) or f"Item #{item_id}"

    def location_name(self, game: str, checksum: str, location_id: int) -> str:
        data = self._data(game, checksum)
        return (data and data.locations.get(location_id)) or f"Lieu #{location_id}"


def _load(path: Path) -> GameData:
    return GameData.from_wire(json.loads(path.read_text("utf-8")))


def _save(path: Path, raw: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(raw), "utf-8")
