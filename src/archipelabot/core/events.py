from dataclasses import dataclass, field
from enum import StrEnum

from ..ap.protocol import ClientStatus, HintStatus, ItemFlags


class ConnectionState(StrEnum):
    CONNECTING = "connecting"
    CONNECTED = "connected"
    RECONNECTING = "reconnecting"
    ASLEEP = "asleep"
    UNREACHABLE = "unreachable"
    FAILED = "failed"
    STOPPED = "stopped"


@dataclass(frozen=True, slots=True)
class ItemSent:
    finder: int
    receiver: int
    item: str
    location: str
    flags: ItemFlags
    location_id: int = field(default=0, compare=False)

    @property
    def self_found(self) -> bool:
        return self.finder == self.receiver


@dataclass(frozen=True, slots=True)
class Released:
    slot: int
    items: tuple[ItemSent, ...] = ()


@dataclass(frozen=True, slots=True)
class Collected:
    slot: int
    items: tuple[ItemSent, ...] = ()


@dataclass(frozen=True, slots=True)
class GoalReached:
    slot: int


@dataclass(frozen=True, slots=True)
class ChatMessage:
    slot: int
    text: str


@dataclass(frozen=True, slots=True)
class Death:
    source: str
    cause: str | None


@dataclass(frozen=True, slots=True)
class PlayerJoined:
    slot: int


@dataclass(frozen=True, slots=True)
class PlayerLeft:
    slot: int


@dataclass(frozen=True, slots=True)
class ClientStatusChanged:
    slot: int
    status: ClientStatus


@dataclass(frozen=True, slots=True)
class HintInfo:
    finder: int  # whose world holds the item
    receiver: int
    item: str
    location: str
    flags: ItemFlags
    status: HintStatus
    found: bool
    entrance: str = ""
    location_id: int = field(default=0, compare=False)

    @property
    def key(self) -> tuple[int, int]:
        return (self.finder, self.location_id)


@dataclass(frozen=True, slots=True)
class HintAdded:
    """Someone hinted an item that hasn't been found yet."""

    hint: HintInfo


@dataclass(frozen=True, slots=True)
class ConnectionChanged:
    state: ConnectionState
    address: str
    detail: str | None = field(default=None, compare=False)


type Event = (
    ItemSent
    | Released
    | Collected
    | GoalReached
    | ChatMessage
    | Death
    | PlayerJoined
    | PlayerLeft
    | ClientStatusChanged
    | HintAdded
    | ConnectionChanged
)
