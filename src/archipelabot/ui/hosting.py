import asyncio
import logging
import secrets
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from ..core.compat import check_options
from ..core.hosting import (
    HostedServer,
    Hosting,
    HostingError,
    ServerOptions,
    extract_output,
    parse_ports,
    pick_port,
    remove,
)
from ..core.logic import DEFAULT_PLANDO, Generated, LogicError, LogicSetup, LogicWorker, find_installs, pick_install
from ..errors import UserError
from ..storage.games import GameRecord
from ..storage.rooms import RoomRecord
from .render.signup import problem_text
from .render.text import md

if TYPE_CHECKING:
    from ..bot import ArchipelaBot

log = logging.getLogger(__name__)

SEED_DIGITS = 20  # like Archipelago's own seeds
MAX_ERRORS = 10


@dataclass(slots=True)
class Generation:
    worker: LogicWorker  # keeps the seed in memory: the room's logic
    generated: Generated
    logic: LogicSetup
    warnings: list[str] = field(default_factory=list)


class HostManager:
    """Seeds the bot generates, and the MultiServer of each active room it hosts."""

    def __init__(self, bot: "ArchipelaBot") -> None:
        self.bot = bot
        self.servers: dict[int, HostedServer] = {}  # room id
        self._reserved: set[int] = set()
        self._lock = asyncio.Lock()

    @property
    def root(self) -> Path:
        return self.bot.settings.database_path.parent / "hosted"

    def folder(self, hosting: Hosting) -> Path:
        return self.root / hosting.folder

    def public_address(self, hosting: Hosting) -> str:
        return f"{self.bot.settings.host_address}:{hosting.port}"

    def check_available(self) -> None:
        if not self.bot.settings.host_address:
            raise UserError("Ce bot n'héberge pas de parties : il faut `HOST_ADDRESS` dans son `.env`.")

    async def generate(self, game: GameRecord) -> Generation:
        entries, files, _ = await self.bot.games.entries(game, used_only=True)
        if not files:
            raise UserError("Personne n'a encore envoyé de yaml.")
        if missing := await self.bot.games.missing(game, files):
            names = ", ".join(f"*{md(name)}*" for name in missing)
            raise UserError(f"Il manque l'apworld de : {names}. Envoyez-le dans le post avant de lancer.")
        settings = self.bot.settings
        installs = await asyncio.to_thread(find_installs, settings.archipelago_path, settings.archipelago_python)
        try:
            install = pick_install(installs, "")
            problems = await check_options(install.python, install.path, entries, DEFAULT_PLANDO)
            if errors := [problem_text(p) for p in problems if p.error]:
                more = f"\n- … et {len(errors) - MAX_ERRORS} autres" if len(errors) > MAX_ERRORS else ""
                raise UserError(
                    "Des yamls ne passent pas avec les apworlds de la partie (bouton **Vérifier** pour le détail) :\n"
                    + "\n".join(f"- {line}" for line in errors[:MAX_ERRORS])
                    + more
                )
            seed = secrets.randbelow(10**SEED_DIGITS)
            worker, generated = await LogicWorker.start(
                install.python, install.path, seed, DEFAULT_PLANDO, entries, output=True
            )
        except LogicError as e:
            raise UserError(f"La génération a échoué. {e}") from e
        try:
            await asyncio.to_thread(extract_output, worker.output_zip(), self.root / generated.seed_name)
        except BaseException:
            await worker.close()
            raise
        logic = LogicSetup(seed, DEFAULT_PLANDO, generated.fingerprint, generated.version)
        return Generation(worker, generated, logic, [problem_text(p) for p in problems])

    async def launch(
        self, generation: Generation, options: ServerOptions, password: str | None
    ) -> tuple[Hosting, HostedServer]:
        """Start the server of a new seed; `adopt` it once its room exists."""
        try:
            ports = parse_ports(self.bot.settings.host_ports)
        except ValueError as e:
            raise UserError(f"`HOST_PORTS` invalide dans le `.env` du bot ({e}).") from e
        async with self._lock:
            used = {*await self.bot.rooms.repo.hosted_ports(), *self._reserved}
            used |= {server.port for server in self.servers.values()}
            try:
                port = await asyncio.to_thread(pick_port, ports, used)
            except HostingError as e:
                raise UserError(str(e)) from e
            self._reserved.add(port)
        try:
            hosting = Hosting(generation.generated.seed_name, port, generation.generated.version, options)
            server = await self._server(hosting, password)
            try:
                await server.start()
            except (HostingError, TimeoutError) as e:
                detail = str(e) if isinstance(e, HostingError) else "il n'a pas démarré à temps."
                raise UserError(f"Impossible de lancer le serveur : {detail}") from e
        finally:
            self._reserved.discard(port)
        return hosting, server

    def adopt(self, room_id: int, server: HostedServer) -> None:
        self.servers[room_id] = server

    async def _server(self, hosting: Hosting, password: str | None) -> HostedServer:
        settings = self.bot.settings
        installs = await asyncio.to_thread(find_installs, settings.archipelago_path, settings.archipelago_python)
        try:
            install = pick_install(installs, hosting.version)
        except LogicError as e:
            raise UserError(str(e)) from e
        return HostedServer(install.python, install.path, self.folder(hosting), hosting, password)

    async def resume(self, record: RoomRecord) -> None:
        """Start the server of a room hosted before a restart, in the background."""
        assert record.hosting is not None and record.id is not None
        try:
            server = await self._server(record.hosting, record.password)
        except UserError as e:
            log.error("Cannot host room %s again: %s", record.id, e)
            return
        server.watch(delay=0)
        self.servers[record.id] = server

    def ensure_running(self, room_id: int) -> None:
        server = self.servers.get(room_id)
        if server is not None and not server.running:
            server.watch(delay=0)

    async def stop(self, room_id: int) -> None:
        if server := self.servers.pop(room_id, None):
            await server.stop()

    async def discard(self, record: RoomRecord) -> None:
        if record.hosting is not None:
            await asyncio.to_thread(remove, self.folder(record.hosting))

    async def shutdown(self) -> None:
        await asyncio.gather(*(self.stop(room_id) for room_id in list(self.servers)))
