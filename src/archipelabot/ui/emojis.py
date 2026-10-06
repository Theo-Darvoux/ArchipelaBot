"""The bot's icons (assets/icons, made by scripts/make_icons.py), uploaded as application emojis.

Renderers use `E.<name>`, which falls back to a Unicode emoji until `ensure_uploaded` has run (and in tests).
Uploaded names carry a hash of the image, so a redrawn icon is uploaded again on the next start. Old versions
are kept: messages already posted still reference them.
"""

import hashlib
import logging
from importlib.resources import files

import discord

log = logging.getLogger(__name__)

PREFIX = "ap_"
RING_STEPS = 8

FALLBACKS: dict[str, str] = {
    "prog": "🟣",
    "useful": "🔵",
    "filler": "⚪",
    "trap": "🔴",
    "online": "🟢",
    "offline": "⚫",
    "goal": "🏆",
    **{f"ring_{i}": "◯◔◔◑◑◕◕◕●"[i] for i in range(RING_STEPS + 1)},
    "release": "📤",
    "collect": "📥",
    "ping": "📬",
    "trophy": "🏆",
    "death": "💀",
    "hint": "💡",
    "chat": "💬",
    "avoid": "⛔",
    "connecting": "⏳",
    "reconnecting": "🟠",
    "asleep": "💤",
    "failed": "🔴",
    "stopped": "⏹️",
    "claim": "🙋",
    "settings": "⚙️",
    "notif_thread": "🔔",
    "notif_dm": "📬",
    "notif_off": "🔕",
    "error": "❌",
    "info": "\N{INFORMATION SOURCE}\N{VARIATION SELECTOR-16}",
    "signup": "📝",
    "yaml": "📄",
    "zip": "📦",
    "random": "🎲",
}


def icon_bytes(name: str) -> bytes:
    return files("archipelabot.assets").joinpath("icons", f"{name}.png").read_bytes()


def uploaded_name(name: str) -> str:
    """e.g. ap_ring_3_1a2b3c: Discord emoji names are limited to 32 characters of [A-Za-z0-9_]."""
    return f"{PREFIX}{name}_{hashlib.sha1(icon_bytes(name)).hexdigest()[:6]}"


class _Emojis:
    def __init__(self) -> None:
        self._uploaded: dict[str, discord.Emoji] = {}

    def __getattr__(self, name: str) -> str:
        if name.startswith("_"):
            raise AttributeError(name)
        return self.get(name)

    def get(self, name: str) -> str:
        emoji = self._uploaded.get(name)
        return str(emoji) if emoji else FALLBACKS[name]

    def partial(self, name: str) -> discord.PartialEmoji:
        """For buttons and select options."""
        emoji = self._uploaded.get(name)
        if emoji is None:
            return discord.PartialEmoji(name=FALLBACKS[name])
        return discord.PartialEmoji(name=emoji.name, id=emoji.id)

    def url(self, name: str) -> str | None:
        """Image URL of an uploaded icon (for thumbnails); None before the upload."""
        emoji = self._uploaded.get(name)
        return emoji.url if emoji else None

    @staticmethod
    def ring_name(ratio: float | None) -> str:
        return f"ring_{round((ratio or 0) * RING_STEPS)}"

    def ring(self, ratio: float | None) -> str:
        return self.get(self.ring_name(ratio))

    def use(self, emojis: dict[str, discord.Emoji]) -> None:
        self._uploaded = dict(emojis)

    def reset(self) -> None:
        self._uploaded = {}


E = _Emojis()


async def ensure_uploaded(client: discord.Client) -> None:
    """Upload missing or redrawn icons, then make renderers use them."""
    wanted = {uploaded_name(name): name for name in FALLBACKS}
    existing = {emoji.name: emoji for emoji in await client.fetch_application_emojis()}
    ready: dict[str, discord.Emoji] = {}
    for emoji_name, name in wanted.items():
        emoji = existing.get(emoji_name)
        if emoji is None:
            emoji = await client.create_application_emoji(name=emoji_name, image=icon_bytes(name))
            log.info("Uploaded application emoji %s", emoji_name)
        ready[name] = emoji
    E.use(ready)
