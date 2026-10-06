import discord
import pytest

from archipelabot.errors import UserError
from archipelabot.ui.forum import TAG_SPECS, RoomTag, ensure_room_tags

from .fakes import FakeForum, FakeInteraction, FakeTag, view_text


def test_commands_are_registered(bot):
    names = {cmd.qualified_name for cmd in bot.tree.walk_commands()}
    assert {"config forum", "config recap", "config annonces", "config voir", "partie nouvelle"} <= names


async def test_ensure_room_tags_creates_only_missing():
    forum = FakeForum(available_tags=[FakeTag("En cours", id=42), FakeTag("Autre")])
    tags = await ensure_room_tags(forum)

    assert tags[RoomTag.ACTIVE].id == 42
    assert [t.name for t in forum.available_tags] == ["En cours", "Autre", "Inscriptions", "Endormie", "Terminée", "Annulée"]
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
