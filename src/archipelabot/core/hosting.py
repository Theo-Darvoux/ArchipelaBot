import asyncio
import contextlib
import logging
import os
import re
import shutil
import socket
import zipfile
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

from .logic import KEPT_ENV

log = logging.getLogger(__name__)

MULTIDATA_SUFFIX = ".archipelago"
SLOT_FILE = re.compile(r"^AP_[^_]+_P(?P<slot>\d+)_")
LOG_NAME = "server.log"
START_TIMEOUT = 90.0
STOP_TIMEOUT = 10.0
RESTART_DELAY = 30.0


class HostingError(Exception):
    pass


@dataclass(frozen=True, slots=True)
class ServerOptions:
    release_mode: str = "auto"
    collect_mode: str = "auto"
    remaining_mode: str = "goal"
    hint_cost: int = 10

    def args(self) -> list[str]:
        return [
            "--release_mode", self.release_mode,
            "--collect_mode", self.collect_mode,
            "--remaining_mode", self.remaining_mode,
            "--hint_cost", str(self.hint_cost),
        ]  # fmt: skip


@dataclass(frozen=True, slots=True)
class Hosting:
    folder: str  # inside the bot's hosted/ folder
    port: int
    version: str  # of Archipelago, that generated the seed
    options: ServerOptions = field(default_factory=ServerOptions)

    @property
    def local_address(self) -> str:
        return f"ws://127.0.0.1:{self.port}"


def parse_ports(text: str) -> range:
    """ "38281-38300" or a single port."""
    first, _, last = text.strip().partition("-")
    ports = range(int(first), int(last or first) + 1)
    if not ports or ports.start < 1 or ports.stop > 65536:
        raise ValueError(f"invalid port range {text!r}")
    return ports


def pick_port(ports: range, used: Iterable[int]) -> int:
    taken = set(used)
    for port in ports:
        if port not in taken and _bindable(port):
            return port
    raise HostingError("Tous les ports du bot pour héberger sont occupés : arrête une room hébergée d'abord.")


def _bindable(port: int) -> bool:
    with socket.socket() as s:
        try:
            s.bind(("", port))
        except OSError:
            return False
    return True


def extract_output(archive: Path, folder: Path) -> None:
    """The multidata, spoiler and players' files of a generation's zip."""
    folder.mkdir(parents=True)
    with zipfile.ZipFile(archive) as z:
        for info in z.infolist():
            name = PurePosixPath(info.filename).name
            if not info.is_dir() and name:
                (folder / name).write_bytes(z.read(info))
    multidata(folder)


def multidata(folder: Path) -> Path:
    found = sorted(folder.glob(f"*{MULTIDATA_SUFFIX}"))
    if len(found) != 1:
        raise HostingError(f"Pas de fichier {MULTIDATA_SUFFIX} dans la génération.")
    return found[0]


def slot_files(folder: Path) -> dict[int, list[Path]]:
    """What each player needs to play (patch, mod…), by slot."""
    files: dict[int, list[Path]] = {}
    for path in sorted(folder.iterdir()) if folder.is_dir() else []:
        if match := SLOT_FILE.match(path.name):
            files.setdefault(int(match["slot"]), []).append(path)
    return files


def remove(folder: Path) -> None:
    shutil.rmtree(folder, ignore_errors=True)


class HostedServer:
    """Archipelago's MultiServer for one room, started again if it stops on its own."""

    def __init__(
        self,
        python: Path,
        archipelago: Path,
        folder: Path,
        hosting: Hosting,
        password: str | None = None,
        *,
        restart_delay: float = RESTART_DELAY,
    ) -> None:
        self.python = python
        self.archipelago = archipelago
        self.folder = folder
        self.port = hosting.port
        self.options = hosting.options
        self.password = password
        self.restart_delay = restart_delay
        self.process: asyncio.subprocess.Process | None = None
        self._watch: asyncio.Task[None] | None = None

    @property
    def running(self) -> bool:
        return self.process is not None and self.process.returncode is None

    async def start(self) -> None:
        await self._spawn()
        self.watch()

    def watch(self, delay: float | None = None) -> None:
        """Start the server again whenever it stops, after `delay` the first time if it isn't running."""
        delay = self.restart_delay if delay is None else delay
        if self._watch:
            self._watch.cancel()
        self._watch = asyncio.create_task(self._keep_running(delay), name=f"server {self.port}")

    async def _spawn(self) -> None:
        args = [str(self.python), "MultiServer.py", str(multidata(self.folder)), "--port", str(self.port)]
        args += self.options.args()
        if self.password:
            args += ["--password", self.password]
        with (self.folder / LOG_NAME).open("ab") as out:
            self.process = await asyncio.create_subprocess_exec(
                *args, cwd=self.archipelago, stdin=asyncio.subprocess.PIPE, stdout=out, stderr=out,
                env={**{k: v for k in KEPT_ENV if (v := os.environ.get(k))}, "SKIP_REQUIREMENTS_UPDATE": "1"},
            )  # fmt: skip
        try:
            await self._wait_ready()
        except BaseException:
            await self._kill()
            raise

    async def _wait_ready(self) -> None:
        assert self.process is not None
        async with asyncio.timeout(START_TIMEOUT):
            while self.process.returncode is None:
                try:
                    _, writer = await asyncio.open_connection("127.0.0.1", self.port)
                except OSError:
                    await asyncio.sleep(0.2)
                    continue
                writer.close()
                return
        raise HostingError(f"Le serveur Archipelago s'est arrêté au démarrage :\n```\n{await self.log_tail()}\n```")

    async def _keep_running(self, delay: float) -> None:
        while True:
            if self.process is not None and self.running:
                await self.process.wait()
                log.warning("Server on port %d stopped (%s), restarting it", self.port, self.process.returncode)
                delay = self.restart_delay
            await asyncio.sleep(delay)
            try:
                await self._spawn()
            except (HostingError, OSError, TimeoutError):
                log.warning("Could not start the server on port %d", self.port, exc_info=True)
                delay = self.restart_delay

    async def command(self, line: str) -> None:
        if self.running and self.process and self.process.stdin:
            self.process.stdin.write(line.encode() + b"\n")
            await self.process.stdin.drain()

    async def stop(self) -> None:
        if self._watch:
            self._watch.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._watch
        if not self.running:
            return
        assert self.process is not None
        try:
            await self.command("/save")
            await self.command("/exit")
            await asyncio.wait_for(self.process.wait(), STOP_TIMEOUT)
        except (ConnectionError, TimeoutError):
            await self._kill()

    async def _kill(self) -> None:
        if self.running and self.process:
            self.process.kill()
            await self.process.wait()

    async def log_tail(self) -> str:
        try:
            text = await asyncio.to_thread((self.folder / LOG_NAME).read_text, errors="replace")
        except OSError:
            return "?"
        return "\n".join(text.strip().splitlines()[-8:])[-1500:]
