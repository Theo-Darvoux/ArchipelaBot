import asyncio

from archipelabot.ap import protocol as p
from archipelabot.ap.client import ConnectOptions, open_session
from archipelabot.ap.protocol import HintStatus, ItemFlags
from archipelabot.core import events as ev
from archipelabot.core.room import RoomState, SlotInfo
from archipelabot.storage.rooms import RoomSettings
from archipelabot.ui.panel_buttons import MyHintsButton
from archipelabot.ui.render.feed import Line, render_event
from archipelabot.ui.render.hints import all_hints_view, player_hints_view
from archipelabot.ui.rooms import RoomManager

from .ap_server import requires_ap_server
from .fakes import FakeInteraction, FakeUser, view_text
from .test_track_cog import discord_env, track  # noqa: F401


def hint(finder, receiver, item, status=HintStatus.UNSPECIFIED, found=False, location=None, entrance=""):
    location_id = location or hash(item) % 10_000
    return ev.HintInfo(finder, receiver, item, f"Lieu {item}", ItemFlags.PROGRESSION, status, found, entrance,
                       location_id)  # fmt: skip


def make_state(*hints: ev.HintInfo) -> RoomState:
    state = RoomState(
        "x:1",
        slots={1: SlotInfo(1, "Alice", "OoT"), 2: SlotInfo(2, "Bob", "SM"), 3: SlotInfo(3, "Carol", "ALttP")},
    )
    state.hints = {h.key: h for h in hints}
    return state


def test_all_hints_grouped_by_finder_with_priorities_first():
    state = make_state(
        hint(1, 2, "Bow"),
        hint(1, 3, "Hookshot", HintStatus.PRIORITY),
        hint(1, 2, "Ice Trap", HintStatus.AVOID),
        hint(3, 1, "Lens", HintStatus.PRIORITY),
        hint(3, 2, "Morph Ball", HintStatus.PRIORITY, entrance="Kakariko"),
        hint(2, 1, "Gone", found=True),
    )
    text = view_text(all_hints_view(state))
    assert text.startswith("### Tous les hints\n-# 5 hints à trouver · 1 trouvés")
    assert "Gone" not in text
    # Carol has two priority hints, Alice one: Carol comes first.
    assert text.index("**Carol** · *ALttP* doit aller chercher") < text.index("**Alice** · *OoT* doit aller chercher")
    alice = text[text.index("**Alice**") :].split("\n")
    assert alice[1:4] == [
        "🟣 **Hookshot** pour Carol · *Lieu Hookshot* · **prioritaire**",
        "🟣 **Bow** pour Bob · *Lieu Bow*",
        "🟣 **Ice Trap** pour Bob · *Lieu Ice Trap* · *à éviter*",
    ]
    assert "**Morph Ball** pour Bob · *Lieu Morph Ball* (entrée : *Kakariko*) · **prioritaire**" in text


def test_empty_and_huge_lists():
    assert "*Aucun hint en attente.*" in view_text(all_hints_view(make_state()))

    many = [hint(1 + i % 3, 1 + (i + 1) % 3, f"Item numéro {i}", location=i + 1) for i in range(300)]
    text = view_text(all_hints_view(make_state(*many)))
    assert len(text) < 4000
    assert text.endswith("autres lignes")


def test_player_hints():
    state = make_state(hint(1, 2, "Bow"), hint(3, 1, "Lens", HintStatus.PRIORITY), hint(2, 3, "Missile"))
    text = view_text(player_hints_view([1], state, you=True))
    assert text.startswith("### Tes hints · Alice\n-# 1 à aller chercher · 1 en attente")
    assert "**À aller chercher dans ton monde**\n🟣 **Bow** pour Bob" in text
    assert "**Ce que tu attends**\n🟣 **Lens** chez Carol · *Lieu Lens* · **prioritaire**" in text
    assert "Missile" not in text
    assert view_text(player_hints_view([1], state, you=False)).startswith("### Hints de Alice")
    assert "Aucun hint en attente" in view_text(player_hints_view([1], make_state(), you=True))


def test_new_hint_feed_line():
    h = hint(1, 2, "Bow")
    state = make_state(h)
    assert render_event(ev.HintAdded(h), state, RoomSettings()) == [
        Line("💡 🟣 **Bow** pour Bob · chez Alice\n-# Lieu Bow")
    ]
    assert render_event(ev.HintAdded(h), state, RoomSettings(show_hints=False)) == []


@requires_ap_server
async def test_hints_on_demand(bot, discord_env, ap_server):  # noqa: F811
    guild, forum = discord_env
    await track(bot, guild, lien=ap_server.address, slot="Alice")
    [thread] = forum.threads
    runtime = bot.rooms.by_thread(thread.id)
    assert runtime.record.hints_message_id is None and len(thread.messages) == 1  # no public board

    carol = await open_session(
        ap_server.address, ConnectOptions(slot="Carol", game="ChecksFinder", tags=(), uuid="carol")
    )
    await carol.send(p.location_scouts_packet(carol.connected.missing_locations))
    info = await anext(pk async for pk in carol if isinstance(pk, p.LocationInfo))
    target = next(i for i in info.locations if i.player == 2)  # an item for Bob
    await carol.send({"cmd": "LocationScouts", "locations": [target.location], "create_as_hint": 2})
    async with asyncio.timeout(10):
        while not runtime.tracker.state.hints:
            await asyncio.sleep(0.05)

    cog = bot.get_cog("status")

    async def hints(user=77, **kwargs) -> dict:
        interaction = FakeInteraction(guild=guild, channel_id=thread.id, user=FakeUser(user))
        await cog.hints.callback(cog, interaction, **{"joueur": None, **kwargs})
        [reply] = interaction.replies
        return reply

    # Without a claim: every hint. With one: yours. Always only visible to the asker.
    reply = await hints()
    assert reply["ephemeral"] and view_text(reply["view"]).startswith("### Tous les hints")
    await runtime.claim(2, 77)
    assert "**Ce que tu attends**" in view_text((await hints())["view"])
    assert view_text((await hints(user=5, joueur="carol"))["view"]).startswith("### Hints de Carol")

    # The panel button shows the same thing.
    interaction = FakeInteraction(user=FakeUser(77))
    interaction.client = bot
    await MyHintsButton(runtime.record.id).callback(interaction)
    assert view_text(interaction.response.sent[0]["view"]).startswith("### Tes hints · Bob")
    await carol.close()


@requires_ap_server
async def test_the_old_public_board_is_deleted(bot, discord_env, ap_server):  # noqa: F811
    guild, forum = discord_env
    await track(bot, guild, lien=ap_server.address, slot="Alice")
    [thread] = forum.threads
    old_board = await thread.send(content="ancien tableau")
    record = bot.rooms.by_thread(thread.id).record
    record.hints_message_id = old_board.id
    await bot.rooms.repo.save_hints_message(record)
    await bot.rooms.shutdown()

    bot.rooms = RoomManager(bot)
    await bot.rooms.restore()
    assert old_board not in thread.messages
    assert (await bot.rooms.repo.get(record.id)).hints_message_id is None
