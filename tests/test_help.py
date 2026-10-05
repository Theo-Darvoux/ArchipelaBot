import discord

from .fakes import FakeInteraction, view_text


async def test_help_lists_every_command_ephemerally(bot):
    cog = bot.get_cog("help")
    interaction = FakeInteraction()

    await cog.help.callback(cog, interaction)

    [reply] = interaction.replies
    assert reply["ephemeral"]
    text = view_text(reply["view"])
    for command in bot.tree.walk_commands():
        if isinstance(command, discord.app_commands.Command) and command.name != "help":
            assert f"`/{command.qualified_name}`" in text


async def test_help_links_synced_commands(bot):
    bot.command_ids = {"track": 123, "help": 456}
    assert bot.command_mention("track start") == "</track start:123>"
    assert bot.command_mention("status") == "`/status`"

    cog = bot.get_cog("help")
    interaction = FakeInteraction()
    await cog.help.callback(cog, interaction)
    assert "</track start:123>" in view_text(interaction.replies[0]["view"])


def test_status_points_to_help(bot):
    assert bot.activity.name == "/help"
