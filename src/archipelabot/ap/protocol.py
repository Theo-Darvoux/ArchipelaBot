"""Archipelago network protocol: the subset of packets the bot sends and receives.

Reference: https://github.com/ArchipelagoMW/Archipelago/blob/main/docs/network%20protocol.md
Unknown fields are ignored so that newer servers don't break parsing.
"""

from enum import IntEnum, IntFlag
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict


class ItemFlags(IntFlag):
    FILLER = 0
    PROGRESSION = 0b001
    USEFUL = 0b010
    TRAP = 0b100


class HintStatus(IntEnum):
    UNSPECIFIED = 0
    NO_PRIORITY = 10
    AVOID = 20
    PRIORITY = 30
    FOUND = 40


class ClientStatus(IntEnum):
    UNKNOWN = 0
    CONNECTED = 5
    READY = 10
    PLAYING = 20
    GOAL = 30


class SlotType(IntFlag):
    SPECTATOR = 0
    PLAYER = 1
    GROUP = 2


class Model(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)


class NetworkVersion(Model):
    major: int
    minor: int
    build: int

    def to_wire(self) -> dict[str, Any]:
        return {"major": self.major, "minor": self.minor, "build": self.build, "class": "Version"}

    def __str__(self) -> str:
        return f"{self.major}.{self.minor}.{self.build}"


class NetworkPlayer(Model):
    team: int
    slot: int
    alias: str
    name: str


class NetworkSlot(Model):
    name: str
    game: str
    type: SlotType
    group_members: list[int] = []


class NetworkItem(Model):
    item: int
    location: int
    player: int
    flags: ItemFlags = ItemFlags.FILLER


class Hint(Model):
    receiving_player: int
    finding_player: int
    location: int
    item: int
    found: bool
    entrance: str = ""
    item_flags: ItemFlags = ItemFlags.FILLER
    status: HintStatus = HintStatus.UNSPECIFIED


class JSONMessagePart(Model):
    type: str | None = None
    text: str | None = None
    color: str | None = None
    flags: int | None = None
    player: int | None = None


# --- Server -> client -----------------------------------------------------------------------------


class RoomInfo(Model):
    cmd: Literal["RoomInfo"]
    version: NetworkVersion
    generator_version: NetworkVersion | None = None
    tags: list[str] = []
    password: bool = False
    games: list[str] = []
    datapackage_checksums: dict[str, str] = {}
    seed_name: str = ""
    time: float = 0.0


class ConnectionRefused(Model):
    cmd: Literal["ConnectionRefused"]
    errors: list[str] = []


class Connected(Model):
    cmd: Literal["Connected"]
    team: int
    slot: int
    players: list[NetworkPlayer]
    missing_locations: list[int] = []
    checked_locations: list[int] = []
    slot_info: dict[int, NetworkSlot] = {}
    hint_points: int = 0


class ReceivedItems(Model):
    cmd: Literal["ReceivedItems"]
    index: int
    items: list[NetworkItem]


class LocationInfo(Model):
    cmd: Literal["LocationInfo"]
    locations: list[NetworkItem]


class RoomUpdate(Model):
    cmd: Literal["RoomUpdate"]
    players: list[NetworkPlayer] | None = None
    checked_locations: list[int] | None = None
    hint_points: int | None = None


class PrintJSON(Model):
    cmd: Literal["PrintJSON"]
    data: list[JSONMessagePart] = []
    type: str | None = None
    receiving: int | None = None
    item: NetworkItem | None = None
    found: bool | None = None
    team: int | None = None
    slot: int | None = None
    message: str | None = None
    tags: list[str] | None = None
    countdown: int | None = None

    @property
    def text(self) -> str:
        return "".join(part.text or "" for part in self.data)


class DataPackage(Model):
    cmd: Literal["DataPackage"]
    data: dict[str, Any]


class Bounced(Model):
    cmd: Literal["Bounced"]
    games: list[str] = []
    slots: list[int] = []
    tags: list[str] = []
    data: dict[str, Any] = {}


class Retrieved(Model):
    model_config = ConfigDict(extra="allow", frozen=True)  # extra args of the Get are echoed back

    cmd: Literal["Retrieved"]
    keys: dict[str, Any]


class SetReply(Model):
    cmd: Literal["SetReply"]
    key: str
    value: Any = None
    original_value: Any = None


class InvalidPacket(Model):
    cmd: Literal["InvalidPacket"]
    type: str = ""
    original_cmd: str | None = None
    text: str = ""


type ServerPacket = (
    RoomInfo
    | ConnectionRefused
    | Connected
    | ReceivedItems
    | LocationInfo
    | RoomUpdate
    | PrintJSON
    | DataPackage
    | Bounced
    | Retrieved
    | SetReply
    | InvalidPacket
)

SERVER_PACKETS: dict[str, type[Model]] = {
    "RoomInfo": RoomInfo,
    "ConnectionRefused": ConnectionRefused,
    "Connected": Connected,
    "ReceivedItems": ReceivedItems,
    "LocationInfo": LocationInfo,
    "RoomUpdate": RoomUpdate,
    "PrintJSON": PrintJSON,
    "DataPackage": DataPackage,
    "Bounced": Bounced,
    "Retrieved": Retrieved,
    "SetReply": SetReply,
    "InvalidPacket": InvalidPacket,
}


def parse_server_packet(raw: dict[str, Any]) -> ServerPacket | None:
    """Parse one packet; returns None for commands the bot doesn't use."""
    cls = SERVER_PACKETS.get(raw.get("cmd", ""))
    return cls.model_validate(raw) if cls else None  # type: ignore[return-value]


# --- Client -> server -----------------------------------------------------------------------------

CLIENT_VERSION = NetworkVersion(major=0, minor=6, build=8)


def connect_packet(
    *,
    name: str,
    password: str | None,
    uuid: str,
    game: str = "",
    tags: list[str],
    items_handling: int = 0b000,
    slot_data: bool = False,
) -> dict[str, Any]:
    return {
        "cmd": "Connect",
        "password": password or "",
        "game": game,
        "name": name,
        "uuid": uuid,
        "version": CLIENT_VERSION.to_wire(),
        "items_handling": items_handling,
        "tags": tags,
        "slot_data": slot_data,
    }


def say_packet(text: str) -> dict[str, Any]:
    return {"cmd": "Say", "text": text}


def get_data_package_packet(games: list[str]) -> dict[str, Any]:
    return {"cmd": "GetDataPackage", "games": games}


def get_packet(keys: list[str], **extra: Any) -> dict[str, Any]:
    return {"cmd": "Get", "keys": keys, **extra}


def set_notify_packet(keys: list[str]) -> dict[str, Any]:
    return {"cmd": "SetNotify", "keys": keys}


def location_checks_packet(locations: list[int]) -> dict[str, Any]:
    return {"cmd": "LocationChecks", "locations": locations}


def location_scouts_packet(locations: list[int]) -> dict[str, Any]:
    return {"cmd": "LocationScouts", "locations": locations, "create_as_hint": 0}


def status_update_packet(status: ClientStatus) -> dict[str, Any]:
    return {"cmd": "StatusUpdate", "status": int(status)}


def hints_key(team: int, slot: int) -> str:
    return f"_read_hints_{team}_{slot}"


def client_status_key(team: int, slot: int) -> str:
    return f"_read_client_status_{team}_{slot}"


def death_link_data(source: str, cause: str | None, time: float) -> dict[str, Any]:
    data: dict[str, Any] = {"time": time, "source": source}
    if cause:
        data["cause"] = cause
    return data
