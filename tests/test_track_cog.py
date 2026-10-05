"""/track against a real Archipelago server, with Discord replaced by fakes."""

import asyncio
from datetime import UTC, datetime, timedelta

import discord
import pytest

from archipelabot.ap import protocol as p
from archipelabot.ap.client import ConnectOptions, open_session
from archipelabot.core import events as ev
from archipelabot.errors import UserError
from archipelabot.storage.guilds import GuildConfig
from archipelabot.ui.rooms import RoomManager

from .ap_server import requires_ap_server
from .fakes import FakeForum, FakeGuild, FakeInteraction, FakeUser, view_text

pytestmark = requires_ap_server

CAROL = ConnectOptions(slot="Carol", game="ChecksFinder", tags=(), items_handling=0b111, uuid="carol")


@pytest.fixture
async def discord_env(bot, tmp_path):
    """A guild with a configured forum; channel lookups resolve to the fake threads."""
    guild = FakeGuild()
    forum = FakeForum(id=100, guild=guild)
    guild.channels[forum.id] = forum
    await bot.guild_configs.save(GuildConfig(guild.id, forum_id=forum.id))
    bot.datapackages.cache_dir = tmp_path

    def get_channel(channel_id):
        return guild.channels.get(channel_id) or next((t for t in forum.threads if t.id == channel_id), None)

    bot.get_channel = get_channel
    return guild, forum


async def track(bot, guild, **kwargs) -> FakeInteraction:
    cog = bot.get_cog("track")
    interaction = FakeInteraction(guild=guild)
    await cog.start.callback(cog, interaction, **{"slot": None, "mot_de_passe": None, "nom": None, **kwargs})
    return interaction


async def wait_until(predicate, wait=10.0):
    async with asyncio.timeout(wait):
        while not predicate():
            await asyncio.sleep(0.05)


async def test_track_creates_post_and_posts_items(bot, discord_env, ap_server):
    guild, forum = discord_env
    interaction = await track(bot, guild, lien=ap_server.address, slot="Alice", nom="Async test")

    [thread] = forum.threads
    assert thread.name == "Async test"
    assert [t.name for t in thread.applied_tags] == ["En cours"]
    assert thread.messages[0].pinned
    panel = view_text(thread.messages[0].view)
    assert "🟢 Connecté" in panel and "· Carol · *ChecksFinder*" in panel
    assert "3 joueurs · 3 jeux" in view_text(interaction.followup.sent[0]["view"])

    runtime = bot.rooms.by_thread(thread.id)
    carol = await open_session(ap_server.address, CAROL)
    await carol.send(p.location_checks_packet(carol.connected.missing_locations[:3]), p.say_packet("coucou"))

    async def posted():
        await runtime.feed.flush()
        return any("coucou" in t for t in thread.texts())

    async with asyncio.timeout(10):
        while not await posted():
            await asyncio.sleep(0.1)
    feed = "\n".join(thread.texts())
    assert "Carol" in feed and "💬 **Carol** : coucou" in feed
    await carol.close()


async def test_idle_rooms_are_left_asleep(bot, discord_env, ap_server):
    guild, forum = discord_env
    await track(bot, guild, lien=ap_server.address, slot="Alice")
    runtime = bot.rooms.by_thread(forum.threads[0].id)
    now = datetime.now(UTC)
    runtime.progress_service.last_activity = now - timedelta(hours=23)
    assert runtime.worth_waking(now)
    runtime.progress_service.last_activity = now - timedelta(hours=25)
    assert not runtime.worth_waking(now)


async def test_stop_and_settings_from_the_post(bot, discord_env, ap_server):
    guild, forum = discord_env
    await track(bot, guild, lien=ap_server.address, slot="Alice")
    [thread] = forum.threads
    runtime = bot.rooms.by_thread(thread.id)
    assert thread.name.startswith("Multiworld · 3 joueurs")

    cog = bot.get_cog("track")
    # Someone else, without moderation rights, can't change anything.
    with pytest.raises(UserError, match="Seule la personne"):
        await cog.settings.callback(cog, FakeInteraction(guild=guild, channel_id=thread.id, user=FakeUser(id=5)))

    interaction = FakeInteraction(guild=guild, channel_id=thread.id)
    await cog.settings.callback(cog, interaction)
    settings_view = interaction.response.sent[0]["view"]
    filler_button = next(
        section.accessory for section in settings_view.walk_children()
        if getattr(section, "accessory", None) and "filler" in view_text_of(section)
    )  # fmt: skip
    await filler_button.callback(FakeInteraction())
    assert runtime.record.settings.show_filler
    assert (await bot.rooms.repo.get(runtime.record.id)).settings.show_filler

    interaction = FakeInteraction(guild=guild, channel_id=thread.id)
    await cog.stop.callback(cog, interaction)
    confirm_view = interaction.response.sent[0]["view"]
    labels = [b.label for b in confirm_view.walk_children() if isinstance(b, discord.ui.Button)]
    assert labels == ["Arrêter et publier le récap", "Arrêter sans récap", "Annuler"]
    stop_button = next(b for b in confirm_view.walk_children() if getattr(b, "label", "") == "Arrêter sans récap")
    confirm = interaction  # the ephemeral message's own interaction
    await stop_button.callback(confirm)
    assert "Suivi arrêté" in view_text(confirm.edited[0]["view"])

    assert bot.rooms.by_thread(thread.id) is None
    assert (await bot.rooms.repo.get(runtime.record.id)).status == "stopped"
    assert [t.name for t in thread.applied_tags] == ["Terminée"]
    assert "⏹️ Suivi arrêté" in view_text(thread.messages[0].view)
    assert runtime.tracker.state.connection == ev.ConnectionState.STOPPED


def view_text_of(section) -> str:
    return "\n".join(child.content for child in section.children if hasattr(child, "content"))


async def test_user_errors(bot, discord_env, ap_server):
    guild, _ = discord_env
    with pytest.raises(UserError, match="ndique aussi `slot`"):
        await track(bot, guild, lien=ap_server.address)
    with pytest.raises(UserError, match="Ce slot n'existe pas"):
        await track(bot, guild, lien=ap_server.address, slot="Nobody")
    with pytest.raises(UserError, match="Impossible de joindre"):
        await track(bot, guild, lien="127.0.0.1:1", slot="Alice")

    await track(bot, guild, lien=ap_server.address, slot="Alice")
    with pytest.raises(UserError, match="déjà suivie"):
        await track(bot, guild, lien=ap_server.address, slot="Bob")

    await bot.guild_configs.save(GuildConfig(guild.id))
    with pytest.raises(UserError, match="/config forum"):
        await track(bot, guild, lien=ap_server.address, slot="Alice")


async def test_rooms_resume_after_restart(bot, discord_env, ap_server):
    guild, forum = discord_env
    await track(bot, guild, lien=ap_server.address, slot="Alice")
    await bot.rooms.shutdown()

    bot.rooms = RoomManager(bot)
    await bot.rooms.restore()
    runtime = bot.rooms.by_thread(forum.threads[0].id)
    await wait_until(lambda: runtime.tracker.state.connection == ev.ConnectionState.CONNECTED)
    assert forum.threads[0].pins == 1


async def test_links_that_are_not_rooms(bot, discord_env):
    guild, _ = discord_env
    with pytest.raises(UserError, match="lien d'un tracker"):
        await track(bot, guild, lien="https://archipelago.gg/tracker/AbC")
    with pytest.raises(UserError, match="pas une room"):
        await track(bot, guild, lien="https://archipelago.gg/seed/AbC")


async def test_archived_posts_are_reopened(bot, discord_env, ap_server):
    guild, forum = discord_env
    await track(bot, guild, lien=ap_server.address, slot="Alice")
    [thread] = forum.threads
    thread.archived = True
    runtime = bot.rooms.by_thread(thread.id)
    await runtime.sink.edit_panel(runtime.render_panel())
    assert not thread.archived


class NotFoundResponse:
    status = 404
    reason = "Not Found"


async def test_rooms_stop_when_their_post_is_deleted(bot, discord_env, ap_server):
    guild, forum = discord_env
    await track(bot, guild, lien=ap_server.address, slot="Alice")
    await track(bot, guild, lien=f"127.0.0.1:{ap_server.port}", slot="Bob")
    first, second = (bot.rooms.by_thread(t.id) for t in forum.threads)

    await bot.on_raw_thread_delete(type("Payload", (), {"thread_id": first.record.thread_id})())
    await wait_until(lambda: bot.rooms.get(first.record.id) is None)

    # Deleted while the bot wasn't watching: noticed the next time the bot posts.
    forum.threads.remove(next(t for t in forum.threads if t.id == second.record.thread_id))

    async def fetch_channel(_channel_id):
        raise discord.NotFound(NotFoundResponse(), "Unknown Channel")

    bot.fetch_channel = fetch_channel
    with pytest.raises(discord.NotFound):
        await second.sink.edit_panel(second.render_panel())
    await wait_until(lambda: bot.rooms.get(second.record.id) is None)

    for runtime in (first, second):
        assert (await bot.rooms.repo.get(runtime.record.id)).status == "stopped"


async def test_rooms_idle_for_a_week_are_stopped(bot, discord_env, ap_server):
    guild, forum = discord_env
    await track(bot, guild, lien=ap_server.address, slot="Alice")
    [thread] = forum.threads
    runtime = bot.rooms.by_thread(thread.id)
    now = datetime.now(UTC)
    runtime.progress_service.last_activity = now - timedelta(days=6)
    assert not runtime.abandoned(now)
    runtime.progress_service.last_activity = now - timedelta(days=8)
    assert runtime.abandoned(now)

    bot.rooms._abandon_soon(runtime.record.id, "Aucune activité")
    await wait_until(lambda: bot.rooms.get(runtime.record.id) is None)
    assert any("Aucune activité : suivi arrêté" in text for text in thread.texts())
    assert (await bot.rooms.repo.get(runtime.record.id)).status == "stopped"


async def test_a_room_that_cannot_resume_does_not_block_the_others(bot, discord_env, ap_server):
    guild, _ = discord_env
    await track(bot, guild, lien=ap_server.address, slot="Alice")
    await track(bot, guild, lien=f"127.0.0.1:{ap_server.port}", slot="Bob")
    await bot.rooms.shutdown()

    bot.rooms = RoomManager(bot)
    build = bot.rooms.build_tracker

    def build_tracker(record):
        if record.slot == "Alice":
            raise RuntimeError("corrupted record")
        return build(record)

    bot.rooms.build_tracker = build_tracker
    await bot.rooms.restore()
    assert [r.record.slot for r in bot.rooms._rooms.values()] == ["Bob"]


async def test_stop_buttons_only_work_once(bot, discord_env, ap_server):
    guild, forum = discord_env
    await track(bot, guild, lien=ap_server.address, slot="Alice")
    [thread] = forum.threads
    cog = bot.get_cog("track")
    interaction = FakeInteraction(guild=guild, channel_id=thread.id)
    await cog.stop.callback(cog, interaction)
    view = interaction.response.sent[0]["view"]
    stop_button = next(b for b in view.walk_children() if getattr(b, "label", "") == "Arrêter et publier le récap")

    first, second = FakeInteraction(guild=guild), FakeInteraction(guild=guild)
    await stop_button.callback(first)
    await stop_button.callback(second)
    assert "Suivi arrêté" in view_text(first.edited[0]["view"])
    assert second.response.deferred and not second.edited
    assert all(b.disabled for b in view.walk_children() if isinstance(b, discord.ui.Button))
    assert sum("Récap" in text or "Partie terminée" in text for text in thread.texts()) == 1
