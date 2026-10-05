from collections.abc import Callable

from discord.utils import escape_markdown

from ...ap.protocol import ItemFlags
from ..emojis import E


def md(text: str) -> str:
    return escape_markdown(text)


def plural(count: int, singular: str, plural_form: str | None = None) -> str:
    return f"{count} {singular if count == 1 else plural_form or singular + 's'}"


def item_emoji(flags: ItemFlags) -> str:
    """A dot in the Archipelago text client's colour for this kind of item."""
    if flags & ItemFlags.PROGRESSION:
        return E.prog
    if flags & ItemFlags.USEFUL:
        return E.useful
    if flags & ItemFlags.TRAP:
        return E.trap
    return E.filler


def truncate(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


def fit_lines(lines: list[str], budget: int, overflow: Callable[[int], str]) -> str:
    """Whole lines within `budget` characters, ending with `overflow(hidden)` when some had to go."""
    text = "\n".join(lines)
    if len(text) <= budget:
        return text
    room = budget - len(overflow(len(lines))) - 1
    kept: list[str] = []
    size = 0
    for line in lines:
        if size + len(line) + 1 > room:
            break
        kept.append(line)
        size += len(line) + 1
    return "\n".join([*kept, overflow(len(lines) - len(kept))])
