import re
from collections.abc import Sequence

from ...core.room import BRIDGE_PREFIX

MAX_LENGTH = 600
CUSTOM_EMOJI = re.compile(r"<a?:(\w+):\d+>")
TIMESTAMP = re.compile(r"<t:(\d+)(?::\w)?>")


def is_server_command(text: str) -> bool:
    return text.lstrip().startswith("!")


def chat_text(content: str, attachment_urls: Sequence[str] = ()) -> str | None:
    """A Discord message (with mentions already resolved) as one line of game chat."""
    text = CUSTOM_EMOJI.sub(r":\1:", content)
    text = TIMESTAMP.sub("(date)", text)
    text = " / ".join(line.strip() for line in text.splitlines() if line.strip())
    text = " ".join(part for part in (text, *attachment_urls) if part)
    if not text:
        return None
    return text if len(text) <= MAX_LENGTH else text[: MAX_LENGTH - 1] + "…"


def signed(author: str, text: str) -> str:
    """For messages the bot can't send as the author's own slot."""
    return f"{BRIDGE_PREFIX} {author}: {text}"
