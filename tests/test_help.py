import discord

from archipelabot.ui.render.help import HelpView

from .fakes import FakeInteraction, view_text


def page_texts(view: HelpView) -> list[str]:
    return [page.text for page in view.pages]


async def test_help_lists_every_command_ephemerally(bot):
    cog = bot.get_cog("help")
    interaction = FakeInteraction()

    await cog.help.callback(cog, interaction)

    [reply] = interaction.replies
    assert reply["ephemeral"]
    view = reply["view"]
    text = "\n".join(page_texts(view))
    for command in bot.tree.walk_commands():
        if isinstance(command, discord.app_commands.Command) and command.name != "help":
            assert f"`/{command.qualified_name}`" in text
    assert all(len(page) < 2000 for page in page_texts(view))


async def test_help_categories_are_buttons(bot):
    view = HelpView(bot.command_mention)
    assert "ArchipelaBot\nLe bot suit" in view_text(view)
    view.to_components()

    def buttons():
        return [b for b in view.walk_children() if isinstance(b, discord.ui.Button)]

    assert [b.label for b in buttons()] == ["Accueil", "Organiser", "En jeu", "Gérer", "Admins"]
    assert buttons()[0].disabled

    click = FakeInteraction()
    await buttons()[4].callback(click)
    assert click.response.sent[0]["view"] is view
    assert "**Suivi complet**" in view_text(view) and "`/partie nouvelle`" not in view_text(view)
    assert buttons()[4].disabled and not buttons()[0].disabled


async def test_help_links_synced_commands(bot):
    bot.command_ids = {"track": 123, "help": 456}
    assert bot.command_mention("track start") == "</track start:123>"
    assert bot.command_mention("status") == "`/status`"

    view = HelpView(bot.command_mention)
    assert "</track start:123>" in view_text(view)
