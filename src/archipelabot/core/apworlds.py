import hashlib
import io
import json
import re
import zipfile
import zlib
from dataclasses import dataclass
from datetime import datetime
from pathlib import PurePosixPath

EXTENSION = ".apworld"
MAX_SIZE = 9 * 1024 * 1024  # the zip handed to the host must fit under Discord's 10 MB
MANIFEST = "archipelago.json"
MAX_TEXT = 1024 * 1024
# `game = "Name"` on the World class, for apworlds made before manifests existed.
GAME_ATTRIBUTE = re.compile(r"^[ \t]+game(?:[ \t]*:[ \t]*[\w\[\]]+)?[ \t]*=[ \t]*([\"'])(.+?)\1", re.MULTILINE)
BROKEN_ZIP = (zipfile.BadZipFile, zlib.error, EOFError, OSError, NotImplementedError)


class ApworldError(Exception):
    pass


@dataclass(slots=True)
class Apworld:
    game: str
    module: str  # the folder inside the zip: Archipelago wants the file named after it
    version: str  # world_version from the manifest, "" without one
    sha256: str
    uploaded_by: int
    uploaded_at: datetime | None = None
    id: int | None = None

    @property
    def filename(self) -> str:
        return f"{self.module}{EXTENSION}"

    @property
    def label(self) -> str:
        return f"{self.module} v{self.version}" if self.version else self.module


@dataclass(frozen=True, slots=True)
class Attached:
    apworld: Apworld
    user_id: int


def is_apworld_filename(filename: str) -> bool:
    return filename.lower().endswith(EXTENSION)


def read_apworld(content: bytes, uploaded_by: int) -> Apworld:
    """What an apworld is for, read from its files: its code is never run."""
    if len(content) > MAX_SIZE:
        raise ApworldError(f"fichier trop gros (plus de {MAX_SIZE // 1024 // 1024} Mo).")
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            module = _module(archive)
            game, version = _manifest(archive, module)
            game = game or _game_in_code(archive, module)
    except BROKEN_ZIP as e:
        raise ApworldError("ce n'est pas un apworld valide (zip illisible).") from e
    return Apworld(game, module, version, hashlib.sha256(content).hexdigest(), uploaded_by)


def _module(archive: zipfile.ZipFile) -> str:
    folders = {
        parts[0]
        for name in archive.namelist()
        if len(parts := PurePosixPath(name).parts) > 1 and parts[0] != "__MACOSX"
    }
    if len(folders) != 1:
        raise ApworldError("ce n'est pas un apworld valide (il doit contenir un seul dossier).")
    return folders.pop()


def _text(archive: zipfile.ZipFile, name: str) -> str | None:
    try:
        info = archive.getinfo(name)
    except KeyError:
        return None
    if info.file_size > MAX_TEXT:
        return None
    return archive.read(info).decode("utf-8-sig", errors="replace")


def _manifest(archive: zipfile.ZipFile, module: str) -> tuple[str, str]:
    text = _text(archive, f"{module}/{MANIFEST}")
    if text is None:
        return "", ""
    try:
        data = json.loads(text)
    except ValueError as e:
        raise ApworldError(f"son `{MANIFEST}` est illisible.") from e
    if not isinstance(data, dict):
        raise ApworldError(f"son `{MANIFEST}` est illisible.")
    game, version = data.get("game"), data.get("world_version")
    return (game.strip() if isinstance(game, str) else ""), (str(version).strip() if version else "")


def _game_in_code(archive: zipfile.ZipFile, module: str) -> str:
    games = {match[2] for match in GAME_ATTRIBUTE.finditer(_text(archive, f"{module}/__init__.py") or "")}
    if len(games) != 1:
        raise ApworldError(f"impossible de savoir de quel jeu il s'agit (pas de `{MANIFEST}`).")
    return games.pop()
