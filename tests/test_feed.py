import asyncio

from archipelabot.ap.protocol import ClientStatus, ItemFlags
from archipelabot.core import events as ev
from archipelabot.core.room import RoomState, SlotInfo
from archipelabot.storage.rooms import RoomSettings
from archipelabot.ui.render.feed import Card, Line, Ping, feed_view, render_event
from archipelabot.ui.services.feed import FeedMessage, FeedService, pack_lines
from archipelabot.ui.services.panel import PanelService
from archipelabot.ui.services.presence import Presence

from .fakes import view_text

STATE = RoomState(
    "archipelago.gg:38281",
    slots={
        1: SlotInfo(1, "Alice", "Ocarina of Time"),
        2: SlotInfo(2, "Bob_*x*", "Super Metroid"),
        3: SlotInfo(3, "Carol", "A Link to the Past"),
    },
)
SETTINGS = RoomSettings()


def item(finder=1, receiver=2, name="Hookshot", flags=ItemFlags.PROGRESSION) -> ev.ItemSent:
    return ev.ItemSent(finder, receiver, name, "Kakariko Well", flags)


def render(event, settings=SETTINGS, ping=lambda slot: None):
    """The single output of an event, or None."""
    outputs = render_event(event, STATE, settings, ping)
    assert len(outputs) <= 1
    return outputs[0] if outputs else None


def test_item_lines():
    assert render(item()) == Line("🟣 Alice → **Hookshot** → Bob\\_\\*x\\*\n-# Kakariko Well")
    assert render(item(3, 3, "Bow")) == Line("🟣 Carol a trouvé **Bow**\n-# Kakariko Well")
    assert render(item(flags=ItemFlags.TRAP)).text.startswith("🔴")
    assert render(item(flags=ItemFlags.USEFUL)).text.startswith("🔵")


def test_filters():
    assert render(item(flags=ItemFlags.FILLER)) is None
    assert render(item(flags=ItemFlags.FILLER), RoomSettings(show_filler=True)).text.startswith("⚪")
    assert render(ev.PlayerJoined(1)) is None
    assert render(ev.PlayerJoined(1), RoomSettings(show_joins=True)) == Line("-# 🟢 Alice a rejoint la partie")
    assert render(ev.Death("Bob", None), RoomSettings(show_deaths=False)) is None


def test_bulk_and_key_events():
    released = ev.Released(1, (item(1, 2), item(1, 3), item(1, 2)))
    assert render(released) == Line("📤 **Alice** a release · 3 items envoyés à 2 joueurs")
    assert render(ev.Collected(2, (item(1, 2),))).text.endswith("1 item récupéré")
    assert render(ev.Released(1)).text.endswith("plus rien à envoyer")

    goal = render(ev.GoalReached(3))
    assert isinstance(goal, Card) and "Carol a terminé" in view_text(goal.view())

    assert render(ev.ChatMessage(2, "salut **toi**")) == Line(r"💬 **Bob\_\*x\*** : salut \*\*toi\*\*")
    assert render(ev.Death("Bob", "Bob fell")) == Line("💀 Mort de **Bob** · « Bob fell »")


def test_pack_lines_respects_the_limits_and_keeps_mentions():
    lines = [Line(f"ligne {i:03}", Ping(i, ()) if i % 10 == 0 else None) for i in range(45)]
    messages = list(pack_lines(lines, max_lines=20, max_chars=1000))
    assert [len(m.lines) for m in messages] == [20, 20, 5]
    assert [line for m in messages for line in m.lines] == [line.text for line in lines]
    assert [m.mentions for m in messages] == [(0, 10), (20, 30), (40,)]

    by_size = list(pack_lines([Line("x" * 40)] * 5, max_chars=100))
    assert [len(m.lines) for m in by_size] == [2, 2, 1]
    assert list(pack_lines([Line("y" * 150)], max_chars=100)) == [FeedMessage(("y" * 99 + "…",))]


def test_feed_view_has_one_text_per_line():
    view = feed_view(["a\n-# lieu", "b", "c"])
    assert [item.content for item in view.children] == ["a\n-# lieu", "b", "c"]


def test_progression_items_ping_their_receiver():
    ping = {2: 222}.get  # Bob is claimed, Carol isn't
    line = render(item(1, 2), ping=ping)
    assert line == Line("🟣 Alice → **Hookshot** → <@222>\n-# Kakariko Well", Ping(222, (item(1, 2),)))
    assert render(item(1, 3), ping=ping).ping is None
    assert render(item(1, 2, flags=ItemFlags.USEFUL), ping=ping).ping is None  # only progression pings
    assert render(item(2, 2), ping=ping).ping is None  # finding your own item doesn't ping

    hidden = RoomSettings(show_progression=False)
    assert render(item(1, 2), hidden, ping).ping.user == 222  # a ping is never filtered out
    assert render(item(1, 3), hidden, ping) is None


def test_release_pings_each_claimed_receiver_once():
    items = (*(item(1, 2, f"Item {i}") for i in range(10)), item(1, 3), item(1, 2, "Rupee", ItemFlags.FILLER))
    summary, ping_line = render_event(ev.Released(1, items), STATE, SETTINGS, {2: 222}.get)
    assert summary.text == "📤 **Alice** a release · 12 items envoyés à 2 joueurs"
    assert ping_line.ping == Ping(222, items[:10])
    assert ping_line.text.startswith("📬 <@222> · 10 items de progression grâce à ce release : **Item 0**")
    assert ping_line.text.endswith("**Item 7** et 2 autres")


class Sink:
    def __init__(self) -> None:
        self.sent: list[str] = []
        self.mentions: list[tuple[int, ...]] = []

    async def send_lines(self, message: FeedMessage) -> None:
        self.sent.append("\n".join(message.lines))
        self.mentions.append(message.mentions)

    async def send_view(self, view) -> None:
        self.sent.append(f"[card] {view_text(view)}")


async def test_feed_groups_lines_and_keeps_order_around_cards():
    sink = Sink()
    feed = FeedService(STATE, lambda: SETTINGS, sink)
    for event in (item(), item(name="Bow"), ev.GoalReached(1), item(name="Lens"), item(flags=ItemFlags.FILLER)):
        await feed.handle(event)
    await feed.flush()

    assert len(sink.sent) == 3
    assert "Hookshot" in sink.sent[0] and "Bow" in sink.sent[0]
    assert sink.sent[1].startswith("[card] ### 🏆 Alice a terminé")
    assert "Lens" in sink.sent[2]

    await feed.flush()
    assert len(sink.sent) == 3  # nothing new


def away_feed(statuses):
    now = [0.0]
    state = RoomState("archipelago.gg:38281", slots=dict(STATE.slots), statuses=statuses)
    presence = Presence(state, grace=120, clock=lambda: now[0])
    sink = Sink()
    feed = FeedService(state, lambda: SETTINGS, sink, ping={2: 222, 3: 333}.get, presence=presence)
    return feed, sink, now


async def leave(feed, slot):
    feed.state.statuses[slot] = ClientStatus.UNKNOWN
    await feed.presence.handle(ev.PlayerLeft(slot))


async def test_players_are_pinged_once_per_absence_from_the_game():
    feed, sink, now = away_feed({2: ClientStatus.PLAYING, 3: ClientStatus.GOAL})

    await feed.handle(item(1, 2, "Hookshot"))
    await feed.handle(item(1, 3, "Hammer"))  # Carol is done
    await feed.flush()
    assert sink.mentions == [()]  # Bob is playing: he gets it in game
    assert "<@222>" in sink.sent[0]

    now[0] = 100
    await leave(feed, 2)
    now[0] = 110
    await feed.handle(item(1, 2, "Bow"))
    await feed.flush()
    assert sink.mentions[-1] == ()  # he may just be restarting his game

    now[0] = 230
    await feed.flush()
    assert sink.sent[-1] == "📬 <@222> · 1 item de progression t'attend : **Bow**"
    assert sink.mentions[-1] == (222,)

    now[0] = 240
    await feed.handle(item(1, 2, "Lens"))
    await feed.flush()
    assert sink.mentions[-1] == ()  # already pinged during this absence

    await feed.presence.handle(ev.PlayerJoined(2))
    now[0] = 400
    await leave(feed, 2)
    now[0] = 530
    await feed.handle(item(1, 2, "Flippers"))
    await feed.flush()
    assert sink.mentions[-1] == (222,)  # a new absence: pinged right on the item's line
    assert "Flippers" in sink.sent[-1] and "t'attend" not in sink.sent[-1]


async def test_the_bots_own_connections_are_not_a_return():
    feed, sink, now = away_feed({2: ClientStatus.PLAYING})
    await leave(feed, 2)
    now[0] = 200
    feed.state.statuses[2] = ClientStatus.CONNECTED  # e.g. the baseline sync, tagged Tracker: no PlayerJoined
    await feed.handle(item(1, 2, "Bow"))
    await feed.flush()
    assert sink.mentions == [(222,)]


async def test_a_short_disconnection_is_not_an_absence():
    feed, sink, now = away_feed({2: ClientStatus.PLAYING})
    await leave(feed, 2)
    now[0] = 10
    await feed.handle(item(1, 2, "Bow"))
    await feed.flush()
    now[0] = 30
    await feed.presence.handle(ev.PlayerJoined(2))
    now[0] = 300
    await feed.flush()
    assert sink.mentions == [()]


async def test_a_user_is_pinged_once_even_across_several_messages():
    sink = Sink()
    feed = FeedService(STATE, lambda: SETTINGS, sink, ping={2: 222}.get)
    for i in range(30):
        await feed.handle(item(1, 2, f"Item {i}"))
    await feed.handle(ev.GoalReached(1))
    await feed.handle(item(1, 2, "Last"))
    await feed.flush()
    assert len(sink.mentions) == 3
    assert sink.mentions == [(222,), (), ()]


async def test_feed_reports_only_long_outages():
    now = [0.0]
    sink = Sink()
    feed = FeedService(STATE, lambda: SETTINGS, sink, clock=lambda: now[0])
    conn = lambda state: ev.ConnectionChanged(state, "archipelago.gg:40000")  # noqa: E731

    await feed.handle(conn(ev.ConnectionState.RECONNECTING))
    now[0] = 10
    await feed.handle(conn(ev.ConnectionState.CONNECTED))
    await feed.flush()
    assert sink.sent == []  # a 10 s blip is not worth a message

    await feed.handle(conn(ev.ConnectionState.RECONNECTING))
    now[0] = 20
    await feed.handle(conn(ev.ConnectionState.ASLEEP))
    now[0] = 200
    await feed.handle(conn(ev.ConnectionState.CONNECTED))
    await feed.flush()
    [message] = sink.sent
    assert "endormie" in message and "Reconnecté à la room · `archipelago.gg:40000`" in message


async def test_panel_edits_are_throttled_but_urgent_ones_go_fast():
    edits: list[int] = []
    counter = iter(range(100))

    async def edit(view):
        edits.append(view)

    panel = PanelService(lambda: next(counter), edit, min_interval=0.3, urgent_interval=0.05)
    task = asyncio.create_task(panel.run())
    try:
        panel.request_update()
        await asyncio.sleep(0.02)
        assert edits == [0]  # first edit is immediate

        for _ in range(5):
            panel.request_update()
        await asyncio.sleep(0.1)
        assert edits == [0]  # throttled

        panel.request_update(urgent=True)
        await asyncio.sleep(0.02)
        assert edits == [0, 1]  # urgent: 0.05 s after the previous edit

        panel.request_update()
        await asyncio.sleep(0.35)
        assert edits == [0, 1, 2]
    finally:
        task.cancel()
