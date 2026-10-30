"""Claims and item pings, against a real Archipelago server."""

import asyncio
from types import SimpleNamespace

import discord
import pytest

from archipelabot.ap import protocol as p
from archipelabot.ap.client import ConnectOptions, open_session
from archipelabot.errors import UserError
from archipelabot.storage.claims import NotifMode
from archipelabot.storage.pings import ItemPing
from archipelabot.ui.autocomplete import item_autocomplete
from archipelabot.ui.panel_buttons import ClaimButton, ClaimPicker, claimed_message
from archipelabot.ui.pings import PingsView
from archipelabot.ui.render.text import md

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
            await runtime.flush()
            if predicate(thread):
                return
            await asyncio.sleep(0.1)


async def test_claim_rules(bot, room):
    runtime, thread, guild = room
    interaction = await run_command(bot, "claim", guild, thread, slot="bob")  # case-insensitive
    assert runtime.claims == {2: 77}
    assert "`/ping` pour être mentionné" in view_text(interaction.replies[0]["view"])
    await bot.notif_prefs.set(77, NotifMode.OFF)
    interaction = await run_command(bot, "claim", guild, thread, slot="Bob")
    assert "Tu joues **Bob**" in view_text(interaction.replies[0]["view"])
    assert "désactivé tes pings" in view_text(interaction.replies[0]["view"])

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
    runtime.panel.urgent_interval = 0.05
    await runtime.claim(2, 77)
    view = runtime.render_panel()
    assert "· Bob · *A Short Hike* · ⚫ · <@77>" in view_text(view)
    [button] = [item for item in view.walk_children() if isinstance(item, ClaimButton)]
    assert button.custom_id == f"archipelabot:claim:{runtime.record.id}"
    view.to_components()  # serialisable as a real message

    async with asyncio.timeout(5):  # the panel service edits the real message
        while "<@77>" not in view_text(thread.messages[0].view):
            await asyncio.sleep(0.05)


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


async def test_only_items_asked_for_with_ping_mention_their_receiver(bot, room, ap_server):
    runtime, thread, guild = room
    finder, item = await find_progression_location(ap_server.address, receiver=2)
    name = runtime.tracker._item_name(2, item.item)

    with pytest.raises(UserError, match="clique d'abord sur \\*\\*Je joue"):
        await run_command(bot, "ping", guild, thread, item=name, joueur=None)
    await runtime.claim(2, 77)
    with pytest.raises(UserError, match="Aucun item \\*\\*Zelda's Lullaby\\*\\* dans \\*A Short Hike\\*"):
        await run_command(bot, "ping", guild, thread, item="Zelda's Lullaby", joueur=None)
    interaction = await run_command(bot, "ping", guild, thread, item=name.upper(), joueur=None)
    assert f"Ajouté : **{name}** pour Bob" in view_text(interaction.replies[0]["view"])
    assert await bot.rooms.ping_repo.for_room(runtime.record.id) == {ItemPing(77, 2, name)}

    await finder.send(p.location_checks_packet([item.location]))
    await flush_until(runtime, thread, lambda t: any("<@77>" in text for text in t.texts()))
    [message] = [m for m in thread.messages[1:] if "<@77>" in m.text]
    assert f"**{name}** → <@77>" in message.text
    assert [u.id for u in message.allowed_mentions.users] == [77]
    assert not any("→" in text for text in thread.texts() if "<@77>" not in text)  # no other item in the post
    await finder.close()


async def test_item_autocomplete_lists_the_game_of_the_slot(bot, room):
    runtime, thread, guild = room
    await runtime.claim(2, 77)
    interaction = FakeInteraction(guild=guild, channel_id=thread.id, user=FakeUser(77))
    interaction.client = bot
    interaction.namespace = SimpleNamespace(joueur=None)
    choices = await item_autocomplete(interaction, "")
    assert choices and set(c.value for c in choices) <= set(runtime.item_names(2))
    interaction.namespace = SimpleNamespace(joueur="Carol")
    assert {c.value for c in await item_autocomplete(interaction, "")} <= set(runtime.item_names(3))


async def test_pings_list_and_removal(bot, room):
    runtime, thread, guild = room
    await runtime.claim(2, 77)
    [first, second] = runtime.item_names(3)[:2]
    await run_command(bot, "ping", guild, thread, item=first, joueur="Carol")
    await run_command(bot, "ping", guild, thread, item=second, joueur="Carol")
    interaction = await run_command(bot, "ping", guild, thread, item=None, joueur=None)
    view = interaction.replies[0]["view"]
    assert isinstance(view, PingsView)
    assert f"**{md(first)}** · pour Carol" in view_text(view)
    view.to_components()

    remove = next(b for b in view.walk_children() if getattr(b, "label", "") == "Retirer")
    click = FakeInteraction()
    await remove.callback(click)
    assert runtime.watched == {ItemPing(77, 3, second)}
    assert "retiré" in view_text(click.response.sent[0]["view"])
    assert await bot.rooms.ping_repo.for_room(runtime.record.id) == {ItemPing(77, 3, second)}


async def test_dm_mode_sends_a_dm_instead(bot, room, ap_server):
    runtime, thread, _ = room
    await runtime.claim(2, 77)
    await bot.notif_prefs.set(77, NotifMode.DM)
    finder, item = await find_progression_location(ap_server.address, receiver=2)
    await runtime.watch(ItemPing(77, 2, runtime.tracker._item_name(2, item.item)))
    dms: list[str] = []

    async def send_dm(user_id, view):
        dms.append(f"{user_id}: {view_text(view)}")

    runtime.sink.send_dm = send_dm
    await finder.send(p.location_checks_packet([item.location]))

    async with asyncio.timeout(10):
        while not dms:
            await runtime.flush()
            await asyncio.sleep(0.1)
    assert dms[0].startswith("77: ### 📬 Nouveaux items")
    assert "message privé" in claimed_message(runtime, 2, 77)
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
