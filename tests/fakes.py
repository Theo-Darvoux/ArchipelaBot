"""Minimal stand-ins for discord.py objects, enough to drive cogs without a gateway connection."""

from dataclasses import dataclass, field
from typing import Any

import discord


@dataclass
class FakeTag:
    name: str
    emoji: Any = None
    id: int = 0


@dataclass
class FakeGuild:
    id: int = 1
    me: object = field(default_factory=object)
    channels: dict[int, Any] = field(default_factory=dict)

    def get_channel(self, channel_id: int):
        return self.channels.get(channel_id)


class FakeMessage:
    def __init__(self, id: int, content: str | None = None, view=None, allowed_mentions=None) -> None:
        self.id = id
        self.content = content
        self.view = view
        self.allowed_mentions = allowed_mentions

    @property
    def text(self) -> str:
        """Plain content, or the text of its components."""
        return self.content or (view_text(self.view) if self.view else "")

    async def delete(self) -> None:
        self.thread.messages.remove(self)

    async def edit(self, *, view=None, content=None) -> None:
        self.view = view if view is not None else self.view
        self.content = content if content is not None else self.content


class FakeThread:
    type = discord.ChannelType.public_thread

    def __init__(self, id: int, name: str, parent: "FakeForum", applied_tags: list) -> None:
        self.id = id
        self.name = name
        self.parent = parent
        self.applied_tags = list(applied_tags)
        self.messages: list[FakeMessage] = []

    @property
    def mention(self) -> str:
        return f"<#{self.id}>"

    async def send(self, content: str | None = None, *, view=None, allowed_mentions=None, **_kwargs) -> FakeMessage:
        message = FakeMessage(self.id * 1000 + len(self.messages), content, view, allowed_mentions)
        self.messages.append(message)
        return message

    def get_partial_message(self, message_id: int) -> FakeMessage:
        message = next(m for m in self.messages if m.id == message_id)
        message.thread = self
        return message

    async def edit(self, *, applied_tags=None, **_kwargs) -> None:
        if applied_tags is not None:
            self.applied_tags = list(applied_tags)

    def texts(self) -> list[str]:
        """Feed messages: everything after the panel."""
        return [m.text for m in self.messages[1:]]


@dataclass
class FakeForum:
    type = discord.ChannelType.forum
    id: int = 100
    guild: FakeGuild = field(default_factory=FakeGuild)
    available_tags: list[FakeTag] = field(default_factory=list)
    permissions: discord.Permissions = field(default_factory=discord.Permissions.all)

    @property
    def mention(self) -> str:
        return f"<#{self.id}>"

    def permissions_for(self, _member) -> discord.Permissions:
        return self.permissions

    threads: list[FakeThread] = field(default_factory=list)

    async def create_tag(self, *, name, emoji=None, reason=None) -> FakeTag:
        tag = FakeTag(name, emoji, id=len(self.available_tags) + 1)
        self.available_tags.append(tag)
        return tag

    async def create_thread(self, *, name, view, applied_tags=(), **_kwargs):
        thread = FakeThread(9000 + len(self.threads), name, self, applied_tags)
        message = await thread.send(view=view)
        self.threads.append(thread)
        return type("ThreadWithMessage", (), {"thread": thread, "message": message})()


class FakeResponse:
    def __init__(self) -> None:
        self.sent: list[dict] = []
        self.deferred = False

    def is_done(self) -> bool:
        return self.deferred or bool(self.sent)

    async def send_message(self, **kwargs) -> None:
        self.sent.append(kwargs)

    async def defer(self, **_kwargs) -> None:
        self.deferred = True

    async def edit_message(self, **kwargs) -> None:
        self.sent.append(kwargs)


class FakeFollowup:
    def __init__(self) -> None:
        self.sent: list[dict] = []

    async def send(self, **kwargs) -> None:
        self.sent.append(kwargs)


@dataclass
class FakeUser:
    id: int = 77

    def __str__(self) -> str:
        return "theo"

    @property
    def mention(self) -> str:
        return f"<@{self.id}>"


@dataclass
class FakeInteraction:
    guild_id: int = 1
    guild: FakeGuild | None = None
    channel_id: int | None = None
    user: FakeUser = field(default_factory=FakeUser)
    permissions: discord.Permissions = field(default_factory=discord.Permissions.none)
    response: FakeResponse = field(default_factory=FakeResponse)
    followup: FakeFollowup = field(default_factory=FakeFollowup)
    command: Any = None
    edited: list[dict] = field(default_factory=list)

    async def edit_original_response(self, **kwargs) -> None:
        self.edited.append(kwargs)

    @property
    def replies(self) -> list[dict]:
        return self.response.sent + self.followup.sent


def view_text(view: discord.ui.LayoutView) -> str:
    """All the text displayed by a Components V2 view, in order."""
    return "\n".join(item.content for item in view.walk_children() if isinstance(item, discord.ui.TextDisplay))
