"""Claims and progression pings, against a real Archipelago server."""

import asyncio

import discord
import pytest

from archipelabot.ap import protocol as p
from archipelabot.ap.client import ConnectOptions, open_session
from archipelabot.errors import UserError
from archipelabot.storage.claims import NotifMode
from archipelabot.ui.panel_buttons import ClaimButton, ClaimPicker

from .ap_server import requires_ap_server
from .fakes import FakeInteraction, FakeUser, view_text
from .test_track_cog import discord_env, track  # noqa: F401

pytestmark = requires_ap_server

GAME_CLIENTS = {
    "Alice": ("Celeste 64", 1),
    "Bob": ("A Short Hike", 2),
    "Carol": ("ChecksFinder", 3),
}


async def find_progression_location(address: str, receiver: int):
    """A connected game client and one of its locations holding a progression item for `receiver`."""
    for name, (game, slot) in GAME_CLIENTS.items():
        if slot == receiver:
            continue
        options = ConnectOptions(slot=name, game=game, tags=(), items_handling=0b111, uuid=f"scout-{name}")
        session = await open_session(address, options)
        await session.send(p.location_scouts_packet(session.connected.missing_locations))
        async with asyncio.timeout(5):
            info = None
            while not isinstance(info, p.LocationInfo):
                info = await session.receive()
        for item in info.locations:
            if item.player == receiver and item.flags & p.ItemFlags.PROGRESSION:
                return session, item
        await session.close()
    pytest.skip("the test seed has no progression item for this player in another world")


@pytest.fixture
async def room(bot, discord_env, ap_server):  # noqa: F811
    guild, forum = discord_env
    await track(bot, guild, lien=ap_server.address, slot="Alice")
    [thread] = forum.threads
    return bot.rooms.by_thread(thread.id), thread, guild


async def run_command(bot, name, guild, thread, user=77, permissions=None, **kwargs) -> FakeInteraction:
    interaction = FakeInteraction(guild=guild, channel_id=thread.id, user=FakeUser(user))
    if permissions:
        interaction.permissions = permissions
    command = bot.tree.get_command(name)
    await command.callback(bot.get_cog("claim"), interaction, **kwargs)
    return interaction


async def flush_until(runtime, thread, predicate):
    async with asyncio.timeout(10):
        while True:
            await runtime.feed.flush()
            if predicate(thread):
                return
            await asyncio.sleep(0.1)


async def test_claim_rules(bot, room):
    runtime, thread, guild = room
    interaction = await run_command(bot, "claim", guild, thread, slot="bob")  # case-insensitive
    assert runtime.claims == {2: 77}
    assert "Tu joues **Bob**" in view_text(interaction.replies[0]["view"])

    with pytest.raises(UserError, match="déjà pris par <@77>"):
        await run_command(bot, "claim", guild, thread, user=88, slot="Bob")
    with pytest.raises(UserError, match="Seuls les modérateurs"):
        await run_command(bot, "claim", guild, thread, user=88, slot="Carol", pour=FakeUser(99))
    with pytest.raises(UserError, match="Aucun joueur nommé"):
        await run_command(bot, "claim", guild, thread, slot="Zelda")

    # A moderator can reassign a taken slot.
    mod = discord.Permissions(manage_threads=True)
    await run_command(bot, "claim", guild, thread, user=88, permissions=mod, slot="Bob", pour=FakeUser(99))
    assert runtime.claims == {2: 99}

    await run_command(bot, "claim", guild, thread, user=99, slot="Carol")
    with pytest.raises(UserError, match="plusieurs slots"):
        await run_command(bot, "unclaim", guild, thread, user=99, slot=None)
    await run_command(bot, "unclaim", guild, thread, user=99, slot="Carol")
    assert runtime.claims == {2: 99}
    assert await bot.rooms.claim_repo.for_room(runtime.record.id) == {2: 99}


async def test_panel_shows_claims_and_button(bot, room):
    runtime, thread, _ = room
    await runtime.claim(2, 77)
    view = runtime.render_panel()
    assert "· Bob · *A Short Hike* · ⚫ · <@77>" in view_text(view)
    [button] = [item for item in view.walk_children() if isinstance(item, ClaimButton)]
    assert button.custom_id == f"archipelabot:claim:{runtime.record.id}"
    view.to_components()  # serialisable as a real message

    await asyncio.sleep(3.2)  # the panel service edits the real message (urgent interval)
    assert "<@77>" in view_text(thread.messages[0].view)


async def test_claim_button_picker(bot, room):
    runtime, _, _ = room
    await runtime.claim(1, 55)
    interaction = FakeInteraction(user=FakeUser(77))
    interaction.client = bot
    await ClaimButton(runtime.record.id).callback(interaction)

    picker = interaction.response.sent[0]["view"]
    assert isinstance(picker, ClaimPicker)
    assert [o.label for o in picker.select.options] == ["Bob", "Carol"]  # Alice is taken

    picker.select._values = ["3"]
    answer = FakeInteraction(user=FakeUser(77))
    await picker._picked(answer)
    assert runtime.claims == {1: 55, 3: 77}
    assert "Tu joues **Carol**" in view_text(answer.response.sent[0]["view"])


async def test_progression_item_pings_its_receiver(bot, room, ap_server):
    runtime, thread, _ = room
    await runtime.claim(2, 77)
    finder, item = await find_progression_location(ap_server.address, receiver=2)
    await finder.send(p.location_checks_packet([item.location]))

    await flush_until(runtime, thread, lambda t: any("<@77>" in text for text in t.texts()))
    [message] = [m for m in thread.messages[1:] if "<@77>" in m.text]
    assert [u.id for u in message.allowed_mentions.users] == [77]  # only the receiver is actually pinged
    await finder.close()


async def test_a_player_in_game_is_not_pinged(bot, room, ap_server):
    runtime, thread, _ = room
    await runtime.claim(2, 77)
    bob = await open_session(
        ap_server.address, ConnectOptions(slot="Bob", game="A Short Hike", tags=(), items_handling=0b111, uuid="bob")
    )
    async with asyncio.timeout(5):
        while not runtime.tracker.state.is_online(2):
            await asyncio.sleep(0.05)

    finder, item = await find_progression_location(ap_server.address, receiver=2)
    await finder.send(p.location_checks_packet([item.location]))
    await flush_until(runtime, thread, lambda t: any("<@77>" in text for text in t.texts()))
    [message] = [m for m in thread.messages[1:] if "<@77>" in m.text]
    assert message.allowed_mentions.users == []

    await bob.close()
    async with asyncio.timeout(5):
        while runtime.tracker.state.is_online(2):
            await asyncio.sleep(0.05)
    await finder.close()


async def test_dm_mode_sends_a_dm_instead(bot, room, ap_server):
    runtime, thread, _ = room
    await runtime.claim(2, 77)
    await bot.notif_prefs.set(77, NotifMode.DM)
    dms: list[str] = []

    async def send_dm(user_id, view):
        dms.append(f"{user_id}: {view_text(view)}")

    runtime.sink.send_dm = send_dm
    finder, item = await find_progression_location(ap_server.address, receiver=2)
    await finder.send(p.location_checks_packet([item.location]))

    async with asyncio.timeout(10):
        while not dms:
            await runtime.notify.flush()
            await asyncio.sleep(0.1)
    assert dms[0].startswith("77: ### 📬 Nouveaux items")
    await runtime.feed.flush()
    assert not any("<@77>" in text for text in thread.texts())
    await finder.close()


async def test_known_players_are_claimed_in_the_next_room(bot, discord_env, ap_server):  # noqa: F811
    guild, forum = discord_env
    await track(bot, guild, lien=ap_server.address, slot="Alice")
    first = bot.rooms.by_thread(forum.threads[0].id)
    await first.claim(2, 77)
    await bot.rooms.stop(first, "finished")

    interaction = await track(bot, guild, lien=ap_server.address, slot="Alice")
    second = bot.rooms.by_thread(forum.threads[1].id)
    assert second.claims == {2: 77}
    assert "1 joueur reconnu" in view_text(interaction.followup.sent[0]["view"])
