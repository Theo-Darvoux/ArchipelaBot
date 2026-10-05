import asyncio
import contextlib
import logging
import time
from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field, replace

from ..ap import protocol as p
from ..ap.client import APConnectionError, APError, APRefused, APSession, ConnectOptions, open_session
from ..ap.datapackage import DataPackageStore
from . import events as ev

log = logging.getLogger(__name__)

RETRY_DELAYS = (5, 10, 20, 40, 80, 160, 300)
BULK_QUIET = 1.5
NON_GAME_TAGS = frozenset({"TextOnly", "Tracker", "HintGame"})
BRIDGE_PREFIX = "[Discord]"
WAKE_GRACE = 2

type Listener = Callable[[ev.Event], Awaitable[None]]
type AddressResolver = Callable[[], Awaitable[str | None]]
type Waker = Callable[[], Awaitable[None]]


@dataclass(frozen=True, slots=True)
class SlotInfo:
    slot: int
    name: str  # what clients connect with
    game: str
    is_group: bool = False
    alias: str = ""  # set with !alias, shown by the server as "Alias (Name)"

    @property
    def display(self) -> str:
        return self.alias or self.name


@dataclass
class RoomState:
    address: str
    team: int = 0
    own_slot: int = 0
    slots: dict[int, SlotInfo] = field(default_factory=dict)
    statuses: dict[int, p.ClientStatus] = field(default_factory=dict)
    hints: dict[tuple[int, int], ev.HintInfo] = field(default_factory=dict)  # by (finder, location)
    relay_slots: set[int] = field(default_factory=set)
    games_on_shared: set[int] = field(default_factory=set)
    connection: ev.ConnectionState = ev.ConnectionState.CONNECTING

    def name(self, slot: int) -> str:
        if info := self.slots.get(slot):
            return info.display
        return "Serveur" if slot == 0 else f"Joueur {slot}"

    def slot_name(self, slot: int) -> str:
        info = self.slots.get(slot)
        return info.name if info else self.name(slot)

    def game(self, slot: int) -> str:
        info = self.slots.get(slot)
        return info.game if info else "Archipelago"

    @property
    def players(self) -> list[SlotInfo]:
        return [s for s in self.slots.values() if not s.is_group]

    def slot_by_name(self, name: str) -> int | None:
        return next((s.slot for s in self.slots.values() if name in (s.name, s.display)), None)

    def is_online(self, slot: int) -> bool:
        """A game client is connected to this slot."""
        if self.is_shared(slot):  # the bot's own connection keeps the server status up
            return slot in self.games_on_shared
        status = self.statuses.get(slot, p.ClientStatus.UNKNOWN)
        return status in (p.ClientStatus.CONNECTED, p.ClientStatus.READY, p.ClientStatus.PLAYING)

    def is_shared(self, slot: int) -> bool:
        return slot == self.own_slot or slot in self.relay_slots

    def share(self, slot: int, online: bool) -> None:
        """The bot now connects as `slot` too; `online`: whether a game was connected before it did."""
        if online:
            self.games_on_shared.add(slot)
        else:
            self.games_on_shared.discard(slot)
        self.relay_slots.add(slot)


@dataclass(slots=True)
class _Bulk:
    kind: type[ev.Released] | type[ev.Collected]
    slot: int
    deadline: float
    items: list[ev.ItemSent] = field(default_factory=list)

    def absorbs(self, item: ev.ItemSent) -> bool:
        return (item.finder if self.kind is ev.Released else item.receiver) == self.slot


class RoomTracker:
    def __init__(
        self,
        address: str,
        options: ConnectOptions,
        datapackages: DataPackageStore,
        *,
        resolve_address: AddressResolver | None = None,
        wake: Waker | None = None,
        retry_delays: tuple[float, ...] = RETRY_DELAYS,
        bulk_quiet: float = BULK_QUIET,
    ) -> None:
        self.options = options
        self.datapackages = datapackages
        self.state = RoomState(address)
        self.resolve_address = resolve_address
        self.wake = wake
        self.retry_delays = retry_delays
        self.bulk_quiet = bulk_quiet
        self._listeners: list[Listener] = []
        self._session: APSession | None = None
        self._task: asyncio.Task[None] | None = None
        self._bulk: _Bulk | None = None
        self._said: deque[tuple[int, str]] = deque(maxlen=50)
        self._checksums: dict[str, str] = {}
        self._hints_loaded = False

    def subscribe(self, listener: Listener) -> None:
        self._listeners.append(listener)

    # --- lifecycle ------------------------------------------------------------------------------

    async def start(self, *, listen: bool = True) -> None:
        """Connect; with `listen=False`, packets wait until `listen()` so that no event is emitted unheard."""
        await self._connect()
        await self._set_connection(ev.ConnectionState.CONNECTED)
        if listen:
            self.listen()

    def listen(self) -> None:
        self._spawn(self._session)

    async def stop(self) -> None:
        await self._halt()
        await self._set_connection(ev.ConnectionState.STOPPED)

    async def reconnect(self) -> None:
        """Drop the connection (or give up waiting) and connect again right away."""
        await self._halt()
        await self._set_connection(ev.ConnectionState.CONNECTING)
        self.resume()

    async def _halt(self) -> None:
        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None
        if self._session:
            await self._session.close()
        await self._finish_bulk()

    async def wait_closed(self) -> None:
        if self._task:
            await asyncio.shield(self._task)

    async def say(self, text: str) -> None:
        if self._session is None or self._session.closed:
            raise APConnectionError("not connected")
        await self.send_chat(self._session, self.state.own_slot, text)

    async def send_chat(self, session: APSession, slot: int, text: str) -> None:
        """Say `text` through `session`, connected as `slot`, without relaying the server's echo back."""
        self._said.append((slot, text))
        try:
            await session.send(p.say_packet(text))
        except BaseException:
            self._said.remove((slot, text))
            raise

    async def _connect(self) -> APSession:
        session = await open_session(self.state.address, self.options)
        try:
            self._checksums = session.room_info.datapackage_checksums
            if missing := await self.datapackages.missing(self._checksums):
                await self.datapackages.add(await session.get_data_package(missing))
        except BaseException:
            await session.close()
            raise
        connected = session.connected
        self.state.team, self.state.own_slot = connected.team, connected.slot
        aliases = {player.slot: player.alias for player in connected.players}
        self.state.slots = {
            slot: SlotInfo(slot, info.name, info.game, bool(info.type & p.SlotType.GROUP), aliases.get(slot, ""))
            for slot, info in sorted(connected.slot_info.items())
        }
        try:
            added = await self._watch_storage(session)
        except BaseException:
            await session.close()
            raise
        self._session = session
        for hint in added:
            await self._emit(ev.HintAdded(hint))
        return session

    def _status_keys(self) -> dict[str, int]:
        return {p.client_status_key(self.state.team, slot): slot for slot in self.state.slots}

    def _hint_keys(self) -> set[str]:
        return {p.hints_key(self.state.team, slot) for slot in self.state.slots}

    async def _watch_storage(self, session: APSession) -> list[ev.HintInfo]:
        """Client statuses and hints of every slot, then get notified of their changes.

        Returns the hints added while the bot was disconnected.
        """
        status_keys, hint_keys = self._status_keys(), self._hint_keys()
        values = await session.get([*status_keys, *hint_keys])
        self.state.statuses = {slot: p.ClientStatus(values.get(key) or 0) for key, slot in status_keys.items()}
        self.state.games_on_shared = {
            slot
            for slot in {self.state.own_slot, *self.state.relay_slots}
            if self.state.statuses.get(slot) in (p.ClientStatus.READY, p.ClientStatus.PLAYING)
        }
        hints = {hint.key: hint for key in hint_keys for hint in map(self._hint_info, values.get(key) or [])}
        added = [h for k, h in hints.items() if k not in self.state.hints and not h.found] if self._hints_loaded else []
        self.state.hints, self._hints_loaded = hints, True
        await session.send(p.set_notify_packet([*status_keys, *hint_keys]))
        return added

    def _hint_info(self, raw: dict) -> ev.HintInfo:
        hint = p.Hint.model_validate(raw)
        return ev.HintInfo(
            finder=hint.finding_player,
            receiver=hint.receiving_player,
            item=self._item_name(hint.receiving_player, hint.item),
            location=self._location_name(hint.finding_player, hint.location),
            flags=hint.item_flags,
            status=p.HintStatus.FOUND if hint.found else hint.status,
            found=hint.found,
            entrance=hint.entrance,
            location_id=hint.location,
        )

    def _item_name(self, receiver: int, item_id: int) -> str:
        game = self.state.game(receiver)
        return self.datapackages.item_name(game, self._checksums.get(game, ""), item_id)

    def _location_name(self, finder: int, location_id: int) -> str:
        game = self.state.game(finder)
        return self.datapackages.location_name(game, self._checksums.get(game, ""), location_id)

    async def _update_hints(self, raw_hints: list[dict]) -> None:
        for hint in map(self._hint_info, raw_hints):
            previous = self.state.hints.get(hint.key)
            self.state.hints[hint.key] = hint
            if previous is None and not hint.found:
                await self._emit(ev.HintAdded(hint))

    def resume(self) -> None:
        self._spawn(None)

    def _spawn(self, session: APSession | None) -> None:
        self._task = asyncio.create_task(self._run(session), name=f"room {self.state.address}")
        self._task.add_done_callback(self._on_task_done)

    def _on_task_done(self, task: asyncio.Task[None]) -> None:
        if not task.cancelled() and (error := task.exception()):
            log.error("Tracking of %s stopped unexpectedly", self.state.address, exc_info=error)

    async def _run(self, session: APSession | None) -> None:
        if session is None:
            session = await self._reconnect(wait_first=False)
        while session is not None:
            await self._consume(session)
            log.info("Lost connection to %s", self.state.address)
            await self._set_connection(ev.ConnectionState.RECONNECTING)
            session = await self._reconnect()

    async def _reconnect(self, *, wait_first: bool = True) -> APSession | None:
        attempt = 0 if wait_first else -1
        asleep = failures = 0
        while True:
            if attempt >= 0:
                await asyncio.sleep(self.retry_delays[min(attempt, len(self.retry_delays) - 1)])
            attempt += 1
            if self.resolve_address:
                try:
                    address = await self.resolve_address()
                except Exception:
                    log.warning("Could not resolve the address of %s", self.state.address, exc_info=True)
                    continue
                if address is None:
                    asleep += 1
                    if self.wake is None or asleep > WAKE_GRACE:
                        await self._set_connection(ev.ConnectionState.ASLEEP)
                    if self.wake:
                        try:
                            await self.wake()
                        except Exception:
                            log.warning("Could not wake %s", self.state.address, exc_info=True)
                    continue
                self.state.address = address
            try:
                session = await self._connect()
            except APRefused as e:
                await self._set_connection(ev.ConnectionState.FAILED, str(e))
                return None
            except (APError, OSError, TimeoutError) as e:
                log.debug("Reconnect to %s failed: %s", self.state.address, e)
                failures += 1
                if self.resolve_address is None and failures > WAKE_GRACE:
                    await self._set_connection(ev.ConnectionState.UNREACHABLE)
                continue
            except Exception:
                log.exception("Unexpected error reconnecting to %s", self.state.address)
                continue
            await self._set_connection(ev.ConnectionState.CONNECTED)
            return session

    async def _consume(self, session: APSession) -> None:
        while True:
            timeout = max(0.0, self._bulk.deadline - time.monotonic()) if self._bulk else None
            try:
                packet = await session.receive(timeout)
            except TimeoutError:
                await self._finish_bulk()
                continue
            if packet is None:
                await self._finish_bulk()
                return
            try:
                await self._handle(packet)
            except Exception:
                log.exception("Error handling %s in %s", type(packet).__name__, self.state.address)

    # --- packets -> events ----------------------------------------------------------------------

    async def _handle(self, packet: p.ServerPacket) -> None:
        match packet:
            case p.SetReply(key=key) if key in self._hint_keys():
                await self._update_hints(packet.value or [])
            case p.SetReply(key=key) if (slot := self._status_keys().get(key)) is not None:
                status = p.ClientStatus(packet.value or 0)
                if self.state.statuses.get(slot) != status:
                    self.state.statuses[slot] = status
                    await self._emit(ev.ClientStatusChanged(slot, status))
            case p.PrintJSON(type="ItemSend", item=p.NetworkItem() as item, receiving=int(receiver)):
                await self._item_sent(self._item_event(item, receiver))
            case p.PrintJSON(type="ItemCheat", item=p.NetworkItem() as item, receiving=int(receiver)):
                await self._emit(self._item_event(item, receiver, finder=0))
            case p.PrintJSON(type="Release", slot=int(slot)):
                await self._start_bulk(ev.Released, slot)
            case p.PrintJSON(type="Collect", slot=int(slot)):
                await self._start_bulk(ev.Collected, slot)
            case p.PrintJSON(type="Goal", slot=int(slot)):
                await self._emit(ev.GoalReached(slot))
            case p.PrintJSON(type="Chat", slot=int(slot), message=str(message)):
                if message.startswith("!"):
                    return
                if (slot, message) in self._said:
                    self._said.remove((slot, message))
                    return
                await self._emit(ev.ChatMessage(slot, message))
            case p.PrintJSON(type="ServerChat", message=str(message)):
                await self._emit(ev.ChatMessage(0, message))
            case p.PrintJSON(type="Join", slot=int(slot), tags=tags) if not NON_GAME_TAGS & set(tags or ()):
                self.state.games_on_shared.add(slot)
                await self._emit(ev.PlayerJoined(slot))
            case p.PrintJSON(type="Part", slot=int(slot)) if "has left the game" in packet.text:
                self.state.games_on_shared.discard(slot)
                await self._emit(ev.PlayerLeft(slot))
            case p.Bounced() if "DeathLink" in packet.tags:
                await self._emit(ev.Death(str(packet.data.get("source", "?")), packet.data.get("cause") or None))
            case p.RoomUpdate(players=list(players)):
                for player in players:
                    if (info := self.state.slots.get(player.slot)) and info.alias != player.alias:
                        self.state.slots[player.slot] = replace(info, alias=player.alias)

    def _item_event(self, item: p.NetworkItem, receiver: int, finder: int | None = None) -> ev.ItemSent:
        finder = item.player if finder is None else finder
        return ev.ItemSent(
            finder=finder,
            receiver=receiver,
            item=self._item_name(receiver, item.item),
            location=self._location_name(finder, item.location),
            flags=item.flags,
            location_id=item.location,
        )

    async def _item_sent(self, event: ev.ItemSent) -> None:
        if self._bulk and self._bulk.absorbs(event):
            self._bulk.items.append(event)
            self._bulk.deadline = time.monotonic() + self.bulk_quiet
        else:
            await self._emit(event)

    async def _start_bulk(self, kind: type[ev.Released] | type[ev.Collected], slot: int) -> None:
        await self._finish_bulk()
        self._bulk = _Bulk(kind, slot, time.monotonic() + self.bulk_quiet)

    async def _finish_bulk(self) -> None:
        if bulk := self._bulk:
            self._bulk = None
            await self._emit(bulk.kind(bulk.slot, tuple(bulk.items)))

    # --- events ---------------------------------------------------------------------------------

    async def _set_connection(self, state: ev.ConnectionState, detail: str | None = None) -> None:
        if state == self.state.connection and state != ev.ConnectionState.CONNECTED:
            return
        self.state.connection = state
        await self._emit(ev.ConnectionChanged(state, self.state.address, detail))

    async def _emit(self, event: ev.Event) -> None:
        for listener in self._listeners:
            try:
                await listener(event)
            except Exception:
                log.exception("Listener failed on %s", event)
