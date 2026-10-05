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
