import re
from collections.abc import Iterable
from dataclasses import dataclass, field

import yaml

from .room import SlotInfo

MAX_NAME = 16
MAX_SIZE = 512 * 1024
EXTENSIONS = (".yaml", ".yml")
# Replaced by the generator: {number} counts players with the same name, {player} is the slot number.
PLACEHOLDERS = re.compile(r"\{(?:number|NUMBER|player|PLAYER)\}|%(?:number|player)%")
Loader = getattr(yaml, "CSafeLoader", yaml.SafeLoader)


class YamlError(Exception):
    pass


@dataclass(frozen=True, slots=True)
class PlayerYaml:
    name: str
    game: str | None  # None when the generator picks it at random

    @property
    def templated(self) -> bool:
        return PLACEHOLDERS.search(self.name) is not None

    @property
    def final_name(self) -> str | None:
        return None if self.templated else self.name[:MAX_NAME].strip()

    @property
    def key(self) -> str | None:
        return self.final_name and self.final_name.casefold()

    def matches(self, slot: SlotInfo) -> bool:
        if self.game is not None and self.game != slot.game:
            return False
        if not self.templated:
            return slot.name.casefold() == self.key
        pattern = r"\d*".join(re.escape(part) for part in PLACEHOLDERS.split(self.name))
        return re.fullmatch(pattern, slot.name, re.IGNORECASE) is not None


@dataclass(slots=True)
class YamlFile:
    user_id: int
    filename: str
    content: bytes
    players: list[PlayerYaml] = field(default_factory=list)
    id: int | None = None


def is_yaml_filename(filename: str) -> bool:
    return filename.lower().endswith(EXTENSIONS)


def parse_players(content: bytes) -> list[PlayerYaml]:
    if len(content) > MAX_SIZE:
        raise YamlError(f"Fichier trop gros (plus de {MAX_SIZE // 1024} Ko).")
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError as e:
        raise YamlError("Le fichier n'est pas encodé en UTF-8.") from e
    try:
        documents = [doc for doc in yaml.load_all(text, Loader=Loader) if doc is not None]
    except yaml.YAMLError as e:
        mark = getattr(e, "problem_mark", None)
        where = f" (ligne {mark.line + 1})" if mark else ""
        raise YamlError(f"Ce n'est pas un yaml valide{where}.") from e
    if not documents:
        raise YamlError("Le fichier est vide.")
    return [_player(doc) for doc in documents]


def _player(document: object) -> PlayerYaml:
    if not isinstance(document, dict):
        raise YamlError("Ce n'est pas un yaml de joueur Archipelago.")
    name = document.get("name")
    if not isinstance(name, str) or not name.strip():
        raise YamlError("Il manque le nom du slot (`name`).")
    name = name.strip()
    if name == "Archipelago":
        raise YamlError("Le nom `Archipelago` est réservé par Archipelago.")
    return PlayerYaml(name, _game(document.get("game")))


def _game(value: object) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    if isinstance(value, dict):
        weighted = [str(game) for game, weight in value.items() if isinstance(weight, int | float) and weight > 0]
        if weighted:
            return weighted[0] if len(weighted) == 1 else None
    raise YamlError("Il manque le jeu (`game`).")


def warnings(players: Iterable[PlayerYaml]) -> list[str]:
    return [
        f"`{p.name}` fait plus de {MAX_NAME} caractères : il deviendra `{p.final_name}`."
        for p in players
        if not p.templated and len(p.name) > MAX_NAME
    ]


def replaced_by(files: Iterable[YamlFile], new: YamlFile) -> list[YamlFile]:
    keys = [p.key for p in new.players if p.key]
    if duplicate := next((k for i, k in enumerate(keys) if k in keys[:i]), None):
        name = next(p.final_name for p in new.players if p.key == duplicate)
        raise YamlError(f"Le slot **{name}** apparaît deux fois dans ce fichier.")
    replaced = []
    for old in files:
        overlap = [p.final_name for p in old.players if p.key in keys]
        if old.user_id != new.user_id:
            if overlap:
                raise YamlError(f"Le slot **{overlap[0]}** est déjà pris par <@{old.user_id}>.")
        elif overlap or old.filename == new.filename:
            replaced.append(old)
    return replaced


def assign_slots(files: Iterable[YamlFile], players: Iterable[SlotInfo]) -> dict[int, int]:
    sent = [(player, f.user_id) for f in files for player in f.players]
    claims: dict[int, int] = {}
    for slot in players:
        users = {user for player, user in sent if not player.templated and player.matches(slot)}
        users = users or {user for player, user in sent if player.templated and player.matches(slot)}
        if len(users) == 1:
            claims[slot.slot] = users.pop()
    return claims
