from datetime import UTC, datetime

import discord
import pytest
from discord import app_commands

from archipelabot.errors import UserError
from archipelabot.ui.forum import TAG_SPECS, RoomTag, ensure_room_tags

from .fakes import (
    FakeAttachment,
    FakeForum,
    FakeGuild,
    FakeInteraction,
    FakeTag,
    FakeThread,
    FakeUser,
    FakeUserMessage,
    view_text,
)
from .test_apworlds import apworld


async def test_ensure_room_tags_creates_only_missing():
    forum = FakeForum(available_tags=[FakeTag("En cours", id=42), FakeTag("Autre")])
    tags = await ensure_room_tags(forum)

    assert tags[RoomTag.ACTIVE].id == 42
    names = [t.name for t in forum.available_tags]
    assert names == ["En cours", "Autre", "Inscriptions", "Endormie", "Terminée", "Annulée"]
    assert set(tags) == set(TAG_SPECS)


async def test_set_forum_saves_config_and_creates_tags(bot):
    cog = bot.get_cog("config")
    forum = FakeForum(id=555)
    interaction = FakeInteraction()

    await cog.set_forum.callback(cog, interaction, forum)

    assert (await bot.guild_configs.get(1)).forum_id == 555
    assert len(forum.available_tags) == 5
    [reply] = interaction.replies
    assert reply["ephemeral"] and "<#555>" in view_text(reply["view"])


async def test_set_forum_refuses_without_permissions(bot):
    cog = bot.get_cog("config")
    forum = FakeForum(permissions=discord.Permissions(view_channel=True, send_messages_in_threads=True))

    with pytest.raises(UserError, match="manage_channels") as exc:
        await cog.set_forum.callback(cog, FakeInteraction(), forum)

    assert "create_public_threads" in str(exc.value)
    assert (await bot.guild_configs.get(1)).forum_id is None


async def test_recap_channel_set_and_cleared(bot):
    cog = bot.get_cog("config")

    class Channel:
        id = 7
        mention = "<#7>"

    await cog.set_recap.callback(cog, FakeInteraction(), Channel())
    assert (await bot.guild_configs.get(1)).recap_channel_id == 7

    interaction = FakeInteraction()
    await cog.set_recap.callback(cog, interaction, None)
    assert (await bot.guild_configs.get(1)).recap_channel_id is None
    assert "uniquement" in view_text(interaction.replies[0]["view"])


async def test_show_points_to_setup_when_unconfigured(bot):
    cog = bot.get_cog("config")
    interaction = FakeInteraction()
    await cog.show.callback(cog, interaction)
    assert "/config forum" in view_text(interaction.replies[0]["view"])


async def test_user_errors_are_shown_ephemerally(bot):
    interaction = FakeInteraction()
    await bot.tree.on_error(interaction, UserError("Room introuvable"))
    [reply] = interaction.replies
    assert reply["ephemeral"] and "Room introuvable" in view_text(reply["view"])


async def test_ping_role_set_and_cleared(bot):
    cog = bot.get_cog("config")

    class Role:
        id = 9
        mention = "<@&9>"

    await cog.set_ping_role.callback(cog, FakeInteraction(), Role())
    assert (await bot.guild_configs.get(1)).ping_role_id == 9
    interaction = FakeInteraction()
    await cog.show.callback(cog, interaction)
    assert "<@&9>" in view_text(interaction.replies[0]["view"])

    await cog.set_ping_role.callback(cog, FakeInteraction(), None)
    assert (await bot.guild_configs.get(1)).ping_role_id is None


class ApworldChannel:
    def __init__(self, messages, permissions=None) -> None:
        self.id = 8
        self.mention = "<#8>"
        self.guild = FakeGuild()
        self.messages = messages
        self.permissions = permissions or discord.Permissions.all()

    def permissions_for(self, _member) -> discord.Permissions:
        return self.permissions

    async def history(self, limit=None, oldest_first=False):
        assert limit is None and oldest_first
        for message in self.messages:
            yield message


def posted(channel_id, user, day, *attachments) -> FakeUserMessage:
    message = FakeUserMessage(
        FakeThread(channel_id, "", None, []), attachments=list(attachments), author=FakeUser(user)
    )
    message.created_at = datetime(2026, 10, day, 17, 16, tzinfo=UTC)
    return message


async def test_apworld_channel_is_imported_then_followed(bot):
    cog = bot.get_cog("config")
    spire = FakeAttachment("spire.apworld", apworld("spire", "Slay the Spire"))
    channel = ApworldChannel(
        [
            posted(8, 1, 16, FakeAttachment("hacknet.apworld", apworld(version="0.3.0"))),
            posted(8, 2, 17, spire, FakeAttachment("notes.txt", b"x")),
            posted(8, 3, 17, FakeAttachment("broken.apworld", b"")),
            posted(8, 4, 18, FakeAttachment("hacknet.apworld", apworld())),
            posted(8, 5, 18, FakeAttachment("spire_copy.apworld", spire.content)),
        ]
    )
    with pytest.raises(UserError, match="read_message_history"):
        await cog.set_apworlds.callback(
            cog, FakeInteraction(), ApworldChannel([], discord.Permissions(view_channel=True))
        )

    interaction = FakeInteraction()
    await cog.set_apworlds.callback(cog, interaction, channel)
    text = view_text(interaction.followup.sent[0]["view"])
    assert "3 nouveaux, 1 déjà sur le serveur" in text and "`broken.apworld` : ce n'est pas un apworld valide" in text
    assert (await bot.guild_configs.get(1)).apworld_channel_id == 8
    library = await bot.games.apworlds.library(1, ["Hacknet", "Slay the Spire"])
    assert [(a.label, a.uploaded_by, a.uploaded_at.day) for a in library] == [
        ("Hacknet v0.3.1", 4, 18),
        ("Slay the Spire v0.3.1", 2, 17),
        ("Hacknet v0.3.0", 1, 16),
    ]

    listener = bot.get_cog("apworld")
    message = posted(8, 6, 19, FakeAttachment("tunic.apworld", apworld("tunic", "TUNIC")))
    await listener.on_message(message)
    assert message.reactions == ["✅"] and len(await bot.games.apworlds.library(1, ["TUNIC"])) == 1
    message = posted(8, 6, 19, FakeAttachment("x.apworld", b""))
    await listener.on_message(message)
    assert "Apworld ignoré" in view_text(message.replies[0]["view"])
    message = posted(9, 6, 19, FakeAttachment("other.apworld", apworld("other", "Other")))
    await listener.on_message(message)
    assert not message.reactions and await bot.games.apworlds.library(1, ["Other"]) == []

    interaction = FakeInteraction()
    await cog.show.callback(cog, interaction)
    assert "**Salon des apworlds** : <#8>" in view_text(interaction.replies[0]["view"])
    await cog.set_apworlds.callback(cog, FakeInteraction(), None)
    assert (await bot.guild_configs.get(1)).apworld_channel_id is None


async def test_creation_restricted_to_admins_then_reopened(bot):
    cog = bot.get_cog("config")

    interaction = FakeInteraction()
    await cog.set_creation.callback(cog, interaction, app_commands.Choice(name="Admins uniquement", value="admins"))
    assert (await bot.guild_configs.get(1)).admin_only_games
    assert "Seuls les admins" in view_text(interaction.replies[0]["view"])
    interaction = FakeInteraction()
    await cog.show.callback(cog, interaction)
    assert "**Création des parties** : admins uniquement" in view_text(interaction.replies[0]["view"])

    await cog.set_creation.callback(cog, FakeInteraction(), app_commands.Choice(name="Tous les membres", value="all"))
    assert not (await bot.guild_configs.get(1)).admin_only_games
