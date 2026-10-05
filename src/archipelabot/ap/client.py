"""WebSocket client for an Archipelago server.

`open_session` performs the handshake and returns an authenticated `APSession`. Iterating the session yields
parsed server packets until the connection closes; reconnecting is the caller's job (see core.room).
"""

import asyncio
import itertools
import json
import logging
import ssl
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

import websockets
from websockets.asyncio.client import ClientConnection

from . import protocol as p

log = logging.getLogger(__name__)

DEFAULT_PORT = 38281
HANDSHAKE_TIMEOUT = 15
REQUEST_TIMEOUT = 30


class APError(Exception):
    pass


class APConnectionError(APError):
    """The server could not be reached, or the connection dropped."""


class APRefused(APError):
    """The server rejected the Connect packet (wrong slot, password...)."""

    def __init__(self, errors: list[str]) -> None:
        super().__init__(", ".join(errors) or "connection refused")
        self.errors = errors


def candidate_urls(address: str) -> list[str]:
    """URLs to try for a user-supplied address. Without a scheme, try TLS first like the official client."""
    address = address.strip().rstrip("/")
    if "://" in address:
        return [address]
    if ":" not in address.rsplit("]", 1)[-1]:
        address = f"{address}:{DEFAULT_PORT}"
    return [f"wss://{address}", f"ws://{address}"]


@dataclass(frozen=True, slots=True)
class ConnectOptions:
    slot: str
    password: str | None = None
    tags: tuple[str, ...] = ("TextOnly",)
    game: str = ""
    items_handling: int = 0
    uuid: str = "archipelabot"


@dataclass(eq=False)
class APSession:
    ws: ClientConnection
    url: str
    room_info: p.RoomInfo
    connected: p.Connected
    _queue: asyncio.Queue[p.ServerPacket | None] = field(default_factory=asyncio.Queue)
    _pending_gets: dict[int, asyncio.Future[dict[str, Any]]] = field(default_factory=dict)
    _pending_data_package: asyncio.Future[dict[str, Any]] | None = None
    _data_package_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    _request_ids: itertools.count = field(default_factory=lambda: itertools.count(1))
    _reader: asyncio.Task[None] | None = None

    def start(self, initial: list[p.ServerPacket]) -> None:
        for packet in initial:
            self._queue.put_nowait(packet)
        self._reader = asyncio.create_task(self._read_loop(), name=f"ap-reader {self.url}")

    @property
    def closed(self) -> bool:
        return self._reader is not None and self._reader.done()

    async def send(self, *packets: dict[str, Any]) -> None:
        try:
            await self.ws.send(json.dumps(list(packets)))
        except websockets.ConnectionClosed as e:
            raise APConnectionError("connection closed") from e

    async def get(self, keys: list[str]) -> dict[str, Any]:
        """Read data storage keys (including the server's `_read_*` keys)."""
        rid = next(self._request_ids)
        future = asyncio.get_running_loop().create_future()
        self._pending_gets[rid] = future
        try:
            await self.send(p.get_packet(keys, archipelabot_rid=rid))
            return await asyncio.wait_for(future, REQUEST_TIMEOUT)
        finally:
            self._pending_gets.pop(rid, None)

    async def get_data_package(self, games: list[str]) -> dict[str, Any]:
        """Fetch the data packages of these games: {game: {item_name_to_id, location_name_to_id, checksum}}."""
        async with self._data_package_lock:  # DataPackage replies carry no request id: one at a time
            self._pending_data_package = asyncio.get_running_loop().create_future()
            try:
                await self.send(p.get_data_package_packet(games))
                return await asyncio.wait_for(self._pending_data_package, REQUEST_TIMEOUT)
            finally:
                self._pending_data_package = None

    async def close(self) -> None:
        await self.ws.close()
        if self._reader:
            await self._reader

    async def receive(self, timeout: float | None = None) -> p.ServerPacket | None:  # noqa: ASYNC109
        """Next packet, or None once the connection is closed. Raises TimeoutError; safe to retry."""
        packet = await asyncio.wait_for(self._queue.get(), timeout)
        if packet is None:
            self._queue.put_nowait(None)  # stay closed for later calls
        return packet

    def __aiter__(self) -> AsyncIterator[p.ServerPacket]:
        return self._iterate()

    async def _iterate(self) -> AsyncIterator[p.ServerPacket]:
        while (packet := await self.receive()) is not None:
            yield packet

    async def _read_loop(self) -> None:
        try:
            async for message in self.ws:
                for raw in json.loads(message):
                    self._dispatch(raw)
        except websockets.ConnectionClosed:
            pass
        except Exception:
            log.exception("Unexpected error reading from %s", self.url)
            await self.ws.close()
        finally:
            error = APConnectionError("connection closed")
            for future in [*self._pending_gets.values(), self._pending_data_package]:
                if future and not future.done():
                    future.set_exception(error)
            self._queue.put_nowait(None)

    def _dispatch(self, raw: dict[str, Any]) -> None:
        try:
            packet = p.parse_server_packet(raw)
        except ValueError:
            log.warning("Ignoring malformed %s packet", raw.get("cmd"), exc_info=True)
            return
        match packet:
            case None:
                return
            case p.Retrieved() if future := self._pending_gets.get(raw.get("archipelabot_rid", -1)):
                if not future.done():
                    future.set_result(packet.keys)
            case p.DataPackage() if self._pending_data_package and not self._pending_data_package.done():
                self._pending_data_package.set_result(packet.data.get("games", {}))
            case p.InvalidPacket():
                log.warning("Server rejected a packet: %s", packet)
            case _:
                self._queue.put_nowait(packet)


async def open_session(address: str, options: ConnectOptions) -> APSession:
    """Connect, authenticate and return a running session. Raises APConnectionError or APRefused."""
    errors: list[str] = []
    for url in candidate_urls(address):
        try:
            ws = await websockets.connect(url, max_size=None, open_timeout=HANDSHAKE_TIMEOUT)
        except (OSError, ssl.SSLError, websockets.WebSocketException, TimeoutError) as e:
            errors.append(f"{url}: {e}")
            continue
        try:
            return await asyncio.wait_for(_handshake(ws, url, options), HANDSHAKE_TIMEOUT)
        except TimeoutError as e:
            await ws.close()
            raise APConnectionError(f"{url}: no answer to the handshake") from e
        except (OSError, websockets.WebSocketException, ValueError, KeyError, TypeError) as e:
            await ws.close()
            raise APConnectionError(f"{url}: invalid handshake ({e})") from e
        except BaseException:
            await ws.close()
            raise
    raise APConnectionError("; ".join(errors))


async def _handshake(ws: ClientConnection, url: str, options: ConnectOptions) -> APSession:
    async def receive() -> list[dict[str, Any]]:
        try:
            return json.loads(await ws.recv())
        except websockets.ConnectionClosed as e:
            raise APConnectionError("connection closed during handshake") from e

    room_info: p.RoomInfo | None = None
    while room_info is None:
        room_info = next((p.RoomInfo.model_validate(r) for r in await receive() if r["cmd"] == "RoomInfo"), None)

    connect = p.connect_packet(
        name=options.slot,
        password=options.password,
        uuid=options.uuid,
        game=options.game,
        tags=list(options.tags),
        items_handling=options.items_handling,
    )
    await ws.send(json.dumps([connect]))

    # Packets that arrive in the same frame as Connected (e.g. ReceivedItems) are kept for the iterator.
    while True:
        batch = await receive()
        for i, raw in enumerate(batch):
            if raw["cmd"] == "ConnectionRefused":
                raise APRefused(p.ConnectionRefused.model_validate(raw).errors)
            if raw["cmd"] == "Connected":
                session = APSession(ws, url, room_info, p.Connected.model_validate(raw))
                rest = [packet for r in batch[i + 1 :] if (packet := p.parse_server_packet(r))]
                session.start(rest)
                log.info("Connected to %s as %s (server %s)", url, options.slot, room_info.version)
                return session
