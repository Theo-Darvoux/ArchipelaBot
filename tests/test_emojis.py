import re
from dataclasses import dataclass

import pytest

from archipelabot.ui.emojis import FALLBACKS, E, ensure_uploaded, icon_bytes, uploaded_name


@dataclass
class FakeEmoji:
    name: str
    id: int

    def __str__(self) -> str:
        return f"<:{self.name}:{self.id}>"

    @property
    def url(self) -> str:
        return f"https://cdn.discordapp.com/emojis/{self.id}.png"


class FakeClient:
    def __init__(self, existing: list[FakeEmoji]) -> None:
        self.emojis = existing
        self.created: list[str] = []

    async def fetch_application_emojis(self):
        return list(self.emojis)

    async def create_application_emoji(self, *, name, image):
        assert image.startswith(b"\x89PNG")
        emoji = FakeEmoji(name, 1000 + len(self.emojis))
        self.emojis.append(emoji)
        self.created.append(name)
        return emoji


@pytest.fixture(autouse=True)
def reset_icons():
    yield
    E.reset()


def test_every_icon_has_an_image_and_a_valid_name():
    for name in FALLBACKS:
        assert icon_bytes(name).startswith(b"\x89PNG")
        assert re.fullmatch(r"ap_[a-z0-9_]{1,22}_[0-9a-f]{6}", uploaded_name(name)), name


async def test_uploads_only_what_is_missing():
    current = FakeEmoji(uploaded_name("prog"), 1)
    client = FakeClient([current, FakeEmoji("ap_prog", 2), FakeEmoji("someone_elses", 3)])
    await ensure_uploaded(client)

    assert uploaded_name("prog") not in client.created
    assert len(client.created) == len(FALLBACKS) - 1
    assert {e.name for e in client.emojis} >= {"ap_prog", "someone_elses"}  # old versions are kept
    assert E.prog == f"<:{current.name}:1>"
    assert E.url("goal").startswith("https://cdn.discordapp.com/emojis/")
    assert E.partial("claim").id is not None
