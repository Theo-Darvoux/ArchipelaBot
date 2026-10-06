from enum import StrEnum
from typing import Protocol

import discord


class RoomTag(StrEnum):
    SIGNUP = "signup"
    ACTIVE = "active"
    ASLEEP = "asleep"
    FINISHED = "finished"
    CANCELLED = "cancelled"


class Post(Protocol):
    guild_id: int
    thread_id: int | None
    panel_message_id: int | None


TAG_SPECS: dict[RoomTag, tuple[str, str]] = {
    RoomTag.SIGNUP: ("Inscriptions", "📝"),
    RoomTag.ACTIVE: ("En cours", "🟢"),
    RoomTag.ASLEEP: ("Endormie", "💤"),
    RoomTag.FINISHED: ("Terminée", "🏁"),
    RoomTag.CANCELLED: ("Annulée", "❌"),
}

# What the bot needs in the forum to create and maintain room posts.
REQUIRED_PERMISSIONS = discord.Permissions(
    view_channel=True,
    manage_channels=True,  # create the forum tags
    create_public_threads=True,
    send_messages_in_threads=True,
    manage_threads=True,  # archive / unarchive posts
    pin_messages=True,  # pin the panel
    embed_links=True,
    attach_files=True,
    add_reactions=True,
    read_message_history=True,
)


def missing_permissions(forum: discord.ForumChannel) -> list[str]:
    granted = forum.permissions_for(forum.guild.me)
    return [name for name, needed in REQUIRED_PERMISSIONS if needed and not getattr(granted, name)]


async def ensure_room_tags(forum: discord.ForumChannel) -> dict[RoomTag, discord.ForumTag]:
    """Return the bot's tags on this forum, creating those that don't exist yet (matched by name)."""
    existing = {tag.name: tag for tag in forum.available_tags}
    tags: dict[RoomTag, discord.ForumTag] = {}
    for key, (name, emoji) in TAG_SPECS.items():
        tag = existing.get(name)
        if tag is None:
            tag = await forum.create_tag(
                name=name, emoji=discord.PartialEmoji(name=emoji), reason="Tags des rooms Archipelago"
            )
        tags[key] = tag
    return tags
