import asyncio
import json
import os
import shutil
import tempfile
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from .apworlds import Apworld
from .logic import KEPT_ENV, LogicError, write_files
from .yamls import PlayerYaml

WORKER = Path(__file__).with_name("check_worker.py")
CHECK_TIMEOUT = 180.0


@dataclass(frozen=True, slots=True)
class Problem:
    filename: str
    player: str | None
    game: str | None
    level: Literal["error", "warning"]
    option: str | None = None  # warning: an option this world doesn't have
    value: str | None = None
    detail: str = ""

    @property
    def error(self) -> bool:
        return self.level == "error"


def version_key(text: str) -> tuple[int, ...] | None:
    parts = text.strip().lstrip("v").split(".")
    if not all(part.isdigit() for part in parts):
        return None
    return tuple(int(part) for part in parts) + (0,) * max(0, 3 - len(parts))


type Conflict = tuple[PlayerYaml, str, str, Apworld]  # player, "min" or "max", version asked, apworld


def version_conflicts(players: Iterable[PlayerYaml], apworlds: Mapping[str, Apworld]) -> list[Conflict]:
    """The world versions yamls ask for (`requires: game:`) that the game's apworlds don't have.

    Read from the files: no code is run."""
    found = []
    for player in players:
        for game, low, high in player.requires:
            apworld = apworlds.get(game)
            if apworld is None or (have := version_key(apworld.version)) is None:
                continue
            if low and (want := version_key(low)) is not None and have < want:
                found.append((player, "min", low, apworld))
            if high and (want := version_key(high)) is not None and have > want:
                found.append((player, "max", high, apworld))
    return found


async def check_options(
    python: Path, archipelago: Path, files: Iterable[tuple[str, bytes]], plando: str
) -> list[Problem]:
    """Load the worlds in Archipelago and check every option of the yamls, with every value they can roll.

    `files`: laid out like the Archipelago folder, Players/ and custom_worlds/. Runs the apworlds' code.
    """
    workdir = Path(await asyncio.to_thread(tempfile.mkdtemp, prefix="archipelabot-check-"))
    try:
        await asyncio.to_thread(write_files, workdir, list(files))
        log = workdir / "worker.log"
        with log.open("wb") as stderr:
            process = await asyncio.create_subprocess_exec(
                str(python), "-I", str(WORKER),
                stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=stderr,
                env={**{k: v for k in KEPT_ENV if (v := os.environ.get(k))}, "SKIP_REQUIREMENTS_UPDATE": "1"},
            )  # fmt: skip
        job = {"archipelago": str(archipelago), "workdir": str(workdir), "plando": plando}
        try:
            out, _ = await asyncio.wait_for(process.communicate(json.dumps(job).encode() + b"\n"), CHECK_TIMEOUT)
        except TimeoutError as e:
            process.kill()
            await process.wait()
            raise LogicError("La vérification des yamls a pris trop de temps.") from e
        lines = out.decode("utf-8", errors="replace").strip().splitlines()
        if not lines:
            tail = "\n".join(log.read_text(errors="replace").strip().splitlines()[-8:])[-1500:]
            raise LogicError(f"Archipelago s'est arrêté :\n```\n{tail}\n```")
        answer = json.loads(lines[-1])
        if "error" in answer:
            raise LogicError(f"Erreur d'Archipelago :\n```\n{answer['error'][:1500]}\n```")
        return [Problem(**problem) for problem in answer["problems"]]
    finally:
        await asyncio.to_thread(shutil.rmtree, workdir, True)
