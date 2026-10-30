import io
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, tzinfo
from typing import TYPE_CHECKING

import discord
from discord import ui

from ..ap.protocol import ItemFlags
from ..storage.history import LoggedEvent, LoggedItem
from .components import Tone
from .emojis import E
from .render.text import item_emoji, md, plural, truncate

if TYPE_CHECKING:
    from .types import Interaction

PAGE_SIZE = 15
MAX_LINE = 240
FILENAME = "historique.txt"


@dataclass(frozen=True, slots=True)
class Entry:
    at: datetime
    slots: frozenset[int]
    emoji: str
    text: str


def item_entry(item: LoggedItem, name: Callable[[int], str]) -> Entry:
    if item.finder == item.receiver:
        text = f"{name(item.finder)} a trouvé {item.item} · {item.location}"
    else:
        text = f"{name(item.finder)} → {item.item} → {name(item.receiver)} · {item.location}"
    return Entry(item.at, frozenset({item.finder, item.receiver}), item_emoji(ItemFlags(item.flags)), text)


def event_entry(event: LoggedEvent, name: Callable[[int], str]) -> Entry | None:
    slot, data = event.slot, event.data
    slots = frozenset() if slot is None else frozenset({slot})
    match event.kind:
        case "goal" if slot is not None:
            return Entry(event.at, slots, E.trophy, f"{name(slot)} a terminé son jeu")
        case "death":
            cause = f" · « {data['cause']} »" if data.get("cause") else ""
            return Entry(event.at, slots, E.death, f"Mort de {data.get('source', '?')}{cause}")
        case "release" if slot is not None:
            return Entry(event.at, slots, E.release, f"{name(slot)} a release · {plural(data.get('count', 0), 'item')}")
        case "collect" if slot is not None:
            return Entry(event.at, slots, E.collect, f"{name(slot)} a collect · {plural(data.get('count', 0), 'item')}")
        case "hint" if slot is not None:
            finder = data.get("finder", 0)
            text = f"Hint : {data.get('item', '?')} pour {name(slot)} · chez {name(finder)}"
            return Entry(event.at, slots | {finder}, E.hint, text)
    return None


def history_entries(
    items: list[LoggedItem], events: list[LoggedEvent], name: Callable[[int], str], slot: int | None = None
) -> list[Entry]:
    """Everything the bot saved about a room, oldest first, only what concerns `slot` if given."""
    entries = [item_entry(i, name) for i in items]
    entries += [e for e in (event_entry(event, name) for event in events) if e is not None]
    entries.sort(key=lambda e: e.at)
    return [e for e in entries if slot is None or slot in e.slots]


def history_file(title: str, entries: list[Entry], tz: tzinfo) -> discord.File:
    lines = [title, ""] + [f"{e.at.astimezone(tz):%Y-%m-%d %H:%M} {e.text}" for e in entries]
    return discord.File(io.BytesIO("\n".join(lines).encode()), FILENAME)


class HistoryView(ui.LayoutView):
    """Most recent first, a page at a time, with the whole history as a text file."""

    def __init__(self, title: str, entries: list[Entry], tz: tzinfo) -> None:
        super().__init__(timeout=900)
        self.title = title
        self.entries = entries
        self.tz = tz
        self.page = 0
        self.pages = max(1, -(-len(entries) // PAGE_SIZE))
        self._build()

    def _lines(self) -> list[str]:
        newest = self.entries[::-1][self.page * PAGE_SIZE : (self.page + 1) * PAGE_SIZE]
        return [f"`{e.at.astimezone(self.tz):%d/%m %H:%M}` {e.emoji} {md(truncate(e.text, MAX_LINE))}" for e in newest]

    def _build(self) -> None:
        self.clear_items()
        header = f"### {E.calendar} {md(self.title)}\n-# {plural(len(self.entries), 'événement')}"
        body = "\n".join(self._lines()) or "Rien d'enregistré pour l'instant."
        previous = ui.Button(label="◀", style=discord.ButtonStyle.secondary, disabled=self.page == 0)
        previous.callback = self._turn(-1)
        counter = ui.Button(label=f"{self.page + 1}/{self.pages}", style=discord.ButtonStyle.secondary, disabled=True)
        following = ui.Button(label="▶", style=discord.ButtonStyle.secondary, disabled=self.page >= self.pages - 1)
        following.callback = self._turn(1)
        download = ui.Button(label="Tout en .txt", style=discord.ButtonStyle.primary, disabled=not self.entries)
        download.callback = self._download
        self.add_item(
            ui.Container(
                ui.TextDisplay(header),
                ui.Separator(),
                ui.TextDisplay(body),
                ui.Separator(),
                ui.ActionRow(previous, counter, following, download),
                accent_colour=Tone.INFO.value,
            )
        )

    def _turn(self, step: int) -> Callable[["Interaction"], Awaitable[None]]:
        async def callback(interaction: "Interaction") -> None:
            self.page = min(max(self.page + step, 0), self.pages - 1)
            self._build()
            await interaction.response.edit_message(view=self)

        return callback

    async def _download(self, interaction: "Interaction") -> None:
        view = ui.LayoutView()
        view.add_item(
            ui.Container(
                ui.TextDisplay(f"### {E.calendar} {md(self.title)}"),
                ui.File(f"attachment://{FILENAME}"),
                accent_colour=Tone.INFO.value,
            )
        )
        file = history_file(self.title, self.entries, self.tz)
        await interaction.response.send_message(view=view, files=[file], ephemeral=True)
