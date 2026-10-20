import asyncio
import io
import json
import logging
import os
import re
import shutil
import tempfile
import zipfile
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from ..ap import protocol as p
from ..ap.client import APConnectionError, ConnectOptions, open_session
from .room import RoomState

log = logging.getLogger(__name__)

WORKER = Path(__file__).with_name("logic_worker.py")
VERSION = re.compile(r'^__version__ = "(?P<version>[^"]+)"', re.MULTILINE)
HEADER = re.compile(r"Archipelago Version (?P<version>\S+)\s+-\s+Seed: (?P<seed>\d+)")
PLANDO = re.compile(r"^Plando Options:[ \t]*(?P<plando>.*?)[ \t]*$", re.MULTILINE)
DEFAULT_PLANDO = "bosses, connections, texts"
SPOILER_SUFFIX = "_spoiler.txt"
MAX_SPOILER = 25 * 1024 * 1024
HEADER_SIZE = 64 * 1024
GENERATE_TIMEOUT = 600.0
REQUEST_TIMEOUT = 120.0
SCOUT_TIMEOUT = 30.0
KEPT_ENV = ("PATH", "HOME", "LANG", "LC_ALL", "TMPDIR")


class LogicError(Exception):
    pass


@dataclass(frozen=True, slots=True)
class Spoiler:
    seed: int
    plando: str
    version: str


@dataclass(frozen=True, slots=True)
class LogicSetup:
    seed: int
    plando: str
    fingerprint: str
    version: str = ""  # of Archipelago, "" before versions were kept: the newest installed


@dataclass(frozen=True, slots=True)
class Install:
    version: str
    path: Path
    python: Path


def find_installs(path: Path | None, python: Path | None = None) -> dict[str, Install]:
    """Archipelago source folders with their .venv: `path` itself, or one per version inside it."""
    if path is None or not path.is_dir():
        return {}
    single = (path / "Utils.py").is_file()
    installs = {}
    for folder in [path] if single else sorted(f for f in path.iterdir() if (f / "Utils.py").is_file()):
        if match := VERSION.search((folder / "Utils.py").read_text(errors="replace")):
            interpreter = python if single and python else folder / ".venv" / "bin" / "python"
            installs[match["version"]] = Install(match["version"], folder, interpreter)
    return installs


def pick_install(installs: dict[str, Install], version: str) -> Install:
    if not installs:
        raise LogicError("Archipelago n'est pas installé avec ce bot : la logique n'est pas disponible.")
    if not version:
        return installs[max(installs, key=_version_key)]
    if version not in installs:
        known = ", ".join(sorted(installs, key=_version_key))
        raise LogicError(
            f"Cette seed a été générée avec Archipelago {version}, que le bot n'a pas (il a : {known}) : la "
            "regénération ne donnerait pas la même seed."
        )
    return installs[version]


def _version_key(version: str) -> tuple[int, ...]:
    return tuple(int(part) if part.isdigit() else 0 for part in version.split("."))


@dataclass(frozen=True, slots=True)
class SlotLogic:
    locations: frozenset[int]  # unchecked, in logic with what the player has received
    goal: bool  # the goal is in logic
    unlocks: tuple[int, ...] = ()  # receiver of each progression item in these locations, other players only

    @property
    def accessible(self) -> int:
        return len(self.locations)

    @property
    def blocked(self) -> bool:
        return not self.locations and not self.goal


def blame(results: dict[int, SlotLogic]) -> tuple[int, set[int]] | None:
    """Who holds, in logic, progression items for the most blocked players, and which ones."""
    blocked = {slot for slot, r in results.items() if r.blocked}
    victims = {slot: blocked.intersection(r.unlocks) for slot, r in results.items()}
    keys = {slot: sum(1 for receiver in r.unlocks if receiver in blocked) for slot, r in results.items()}
    culprit = max(victims, key=lambda slot: (len(victims[slot]), keys[slot]), default=None)
    if culprit is None or not victims[culprit]:
        return None
    return culprit, victims[culprit]


@dataclass(frozen=True, slots=True)
class Generated:
    seed_name: str
    version: str
    fingerprint: str
    slots: dict[int, tuple[str, str]]  # name, game


def read_spoiler(filename: str, content: bytes) -> Spoiler:
    """The seed of a generation, from its spoiler log or the zip that contains it."""
    if len(content) > MAX_SPOILER:
        raise LogicError(f"Fichier trop gros (plus de {MAX_SPOILER // 1024 // 1024} Mo).")
    if filename.lower().endswith(".zip"):
        content = _spoiler_in_zip(content)
    head = content[:HEADER_SIZE].decode("utf-8-sig", errors="replace")
    if not (header := HEADER.search(head)):
        raise LogicError("Ce n'est pas un spoiler log d'Archipelago (`AP_…_Spoiler.txt`).")
    plando = PLANDO.search(head)
    return Spoiler(int(header["seed"]), plando["plando"] if plando else DEFAULT_PLANDO, header["version"])


def _spoiler_in_zip(content: bytes) -> bytes:
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            names = [n for n in archive.namelist() if PurePosixPath(n).name.lower().endswith(SPOILER_SUFFIX)]
            if not names:
                raise LogicError("Ce zip ne contient pas de spoiler log : génère avec `spoiler` à 1 ou plus.")
            return archive.read(names[0])
    except (zipfile.BadZipFile, OSError) as e:
        raise LogicError("Zip illisible.") from e


def check_room(generated: Generated, state: RoomState) -> None:
    """The regenerated seed is the room's: same seed name, same players."""
    if state.seed_name and generated.seed_name != state.seed_name.removeprefix("W"):  # W: generated on a WebHost
        raise LogicError("Ce spoiler n'est pas celui de cette room (la seed ne correspond pas).")
    players = {s.slot: (s.name, s.game) for s in state.players}
    if players and players != generated.slots:
        room, regenerated = Counter(players.values()), Counter(generated.slots.values())
        differences = [f"- {_slot(p)} : dans la room, pas dans les yamls" for p in (room - regenerated).elements()]
        differences += [f"- {_slot(p)} : dans les yamls, pas dans la room" for p in (regenerated - room).elements()]
        if not differences:
            differences = [
                f"- slot {slot} : {_slot(players[slot])} dans la room, {_slot(generated.slots[slot])} regénéré"
                for slot in sorted(players)
                if players[slot] != generated.slots[slot]
            ]
        more = f"\n- … et {len(differences) - 10} autres" if len(differences) > 10 else ""
        raise LogicError(
            "Les yamls du post ne donnent pas les mêmes joueurs que la room : il faut exactement les yamls (et "
            "apworlds) utilisés pour générer.\n" + "\n".join(differences[:10]) + more
        )


def _slot(player: tuple[str, str]) -> str:
    return f"`{player[0]}` (*{player[1]}*)"


async def scout_world(address: str, password: str | None, slot_name: str) -> dict[int, tuple[int, int]]:
    """What every location of a slot holds: {location: (item, receiver)}."""
    options = ConnectOptions(slot=slot_name, password=password, tags=("Tracker",), uuid="archipelabot-logic")
    session = await open_session(address, options)
    try:
        connected = session.connected
        await session.send(p.location_scouts_packet([*connected.checked_locations, *connected.missing_locations]))
        async with asyncio.timeout(SCOUT_TIMEOUT):
            async for packet in session:
                if isinstance(packet, p.LocationInfo):
                    return {i.location: (i.item, i.player) for i in packet.locations}
        raise APConnectionError("connection closed")
    finally:
        await session.close()


class LogicWorker:
    """A seed regenerated by Archipelago in its own process, kept in memory to answer logic questions."""

    def __init__(self, process: asyncio.subprocess.Process, workdir: Path) -> None:
        self.process = process
        self.workdir = workdir
        self._lock = asyncio.Lock()

    @classmethod
    async def start(
        cls, python: Path, archipelago: Path, seed: int, plando: str, files: Iterable[tuple[str, bytes]]
    ) -> tuple["LogicWorker", Generated]:
        """`files`: (path, content) laid out like the Archipelago folder, Players/ and custom_worlds/."""
        workdir = Path(await asyncio.to_thread(tempfile.mkdtemp, prefix="archipelabot-logic-"))
        try:
            await asyncio.to_thread(_write, workdir, list(files))
            with (workdir / "worker.log").open("wb") as stderr:
                process = await asyncio.create_subprocess_exec(
                    str(python), "-I", str(WORKER),
                    stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=stderr,
                    env={**{k: v for k in KEPT_ENV if (v := os.environ.get(k))}, "SKIP_REQUIREMENTS_UPDATE": "1"},
                    limit=64 * 1024 * 1024,
                )  # fmt: skip
        except BaseException:
            await asyncio.to_thread(shutil.rmtree, workdir, True)
            raise
        worker = cls(process, workdir)
        try:
            job = {"archipelago": str(archipelago), "workdir": str(workdir), "seed": seed, "plando": plando}
            ready = (await worker._request(job, GENERATE_TIMEOUT))["ready"]
        except BaseException:
            await worker.close()
            raise
        slots = {int(slot): (info["name"], info["game"]) for slot, info in ready["slots"].items()}
        return worker, Generated(ready["seed_name"], ready["version"], ready["fingerprint"], slots)

    async def evaluate(self, checked: dict[int, Iterable[int]]) -> dict[int, SlotLogic]:
        answer = await self._request({"cmd": "evaluate", "checked": {s: list(ids) for s, ids in checked.items()}})
        return {
            int(slot): SlotLogic(frozenset(info["accessible"]), info["goal"], tuple(info["unlocks"]))
            for slot, info in answer["slots"].items()
        }

    async def placements(self, slot: int) -> dict[int, tuple[int, int]]:
        answer = await self._request({"cmd": "placements", "slot": slot})
        return {int(loc): (item, receiver) for loc, (item, receiver) in answer["placements"].items()}

    async def _request(self, request: dict[str, Any], wait: float = REQUEST_TIMEOUT) -> dict[str, Any]:
        assert self.process.stdin and self.process.stdout
        async with self._lock:
            try:
                self.process.stdin.write(json.dumps(request).encode() + b"\n")
                await self.process.stdin.drain()
                line = await asyncio.wait_for(self.process.stdout.readline(), wait)
            except (ConnectionError, TimeoutError) as e:
                raise LogicError(f"Archipelago ne répond pas ({type(e).__name__}).") from e
        if not line:
            raise LogicError(f"Archipelago s'est arrêté :\n```\n{await self._log_tail()}\n```")
        answer = json.loads(line)
        if "error" in answer:
            raise LogicError(f"Erreur d'Archipelago :\n```\n{answer['error'][:1500]}\n```")
        return answer

    async def _log_tail(self) -> str:
        try:
            text = await asyncio.to_thread((self.workdir / "worker.log").read_text, errors="replace")
        except OSError:
            return "?"
        return "\n".join(text.strip().splitlines()[-8:])[-1500:]

    async def close(self) -> None:
        if self.process.returncode is None:
            self.process.kill()
            await self.process.wait()
        await asyncio.to_thread(shutil.rmtree, self.workdir, True)


def _write(workdir: Path, files: list[tuple[str, bytes]]) -> None:
    for folder in ("Players", "custom_worlds", "output"):
        (workdir / folder).mkdir()
    for path, content in files:
        target = (workdir / path).resolve()
        if not target.is_relative_to(workdir.resolve()):
            raise ValueError(f"unsafe path {path}")
        target.write_bytes(content)
