import asyncio

from archipelabot.ap.protocol import ItemFlags
from archipelabot.core import events as ev
from archipelabot.core.room import RoomState, SlotInfo
from archipelabot.storage.claims import NotifMode
from archipelabot.storage.rooms import RoomSettings
from archipelabot.ui.render.feed import Line, chat_event, feed_view, render_event
from archipelabot.ui.services.feed import FeedMessage, FeedService, pack_lines
from archipelabot.ui.services.panel import PanelService
from archipelabot.ui.services.pings import PingService

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


def render(event, settings=SETTINGS):
    """The single output of an event in the full log, or None."""
    outputs = render_event(event, STATE, settings)
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
    assert render(ev.Death("Bob", "Bob fell")) == Line("💀 Mort de **Bob** · « Bob fell »")
    assert render(ev.GoalReached(1)) == Line("🏆 Alice a terminé *Ocarina of Time* !")
    assert render(ev.ChatMessage(2, "salut")) is None  # the chat stays in the room's post


def test_the_room_post_keeps_only_chat_and_goals():
    def chat(event, settings=SETTINGS):
        return chat_event(event, STATE, settings)

    assert chat(ev.ChatMessage(2, "salut **toi**")) == [Line(r"💬 **Bob\_\*x\*** : salut \*\*toi\*\*")]
    assert chat(ev.ChatMessage(2, "salut"), RoomSettings(chat_bridge=False)) == []
    assert chat(ev.GoalReached(1)) == [Line("🏆 Alice a terminé *Ocarina of Time* !")]
    for event in (item(), ev.Released(1, (item(),)), ev.Death("Bob", None), ev.PlayerJoined(1), ev.Collected(1)):
        assert chat(event, RoomSettings(show_joins=True)) == []


def test_pack_lines_respects_the_limits_and_keeps_mentions():
    lines = [Line(f"ligne {i:03}", i if i % 10 == 0 else None) for i in range(45)]
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


class Sink:
    def __init__(self) -> None:
        self.sent: list[str] = []
        self.mentions: list[tuple[int, ...]] = []
        self.dms: list[str] = []

    async def send_lines(self, message: FeedMessage) -> None:
        self.sent.append("\n".join(message.lines))
        self.mentions.append(message.mentions)

    async def send_view(self, view) -> None:
        self.sent.append(f"[card] {view_text(view)}")

    async def send_dm(self, user_id, view) -> None:
        self.dms.append(f"{user_id}: {view_text(view)}")


def ping_service(watched: dict[tuple[int, str], list[int]], modes=None, claims=None):
    sink = Sink()
    service = PingService(
        STATE,
        lambda i: watched.get((i.receiver, i.item), []),
        lambda user: (modes or {}).get(user, NotifMode.THREAD),
        lambda user, slot: (claims or {}).get(slot) == user,
        sink,
        lambda: "Async",
        lambda: "https://discord.com/channels/1/2",
    )
    return service, sink


async def test_only_the_items_asked_for_ping():
    pings, sink = ping_service({(2, "Hookshot"): [222], (3, "Bow"): [222]}, claims={2: 222})
    await pings.handle(item(1, 2, "Hookshot"))
    await pings.handle(item(1, 2, "Rupee", ItemFlags.FILLER))  # not asked for
    await pings.handle(item(1, 3, "Bow"))  # Carol's, watched by Bob's player
    await pings.handle(item(2, 2, "Hookshot"))  # found by Bob himself
    await pings.handle(ev.GoalReached(1))
    await pings.flush()
    assert sink.sent == [
        "🟣 Alice → **Hookshot** → <@222>\n-# Kakariko Well\n📬 <@222> · 🟣 Alice → **Bow** → Carol\n-# Kakariko Well"
    ]
    assert sink.mentions == [(222,)]

    await pings.flush()
    assert len(sink.sent) == 1  # nothing new


async def test_released_items_ping_too_and_each_user_once_per_message():
    pings, sink = ping_service({(2, f"Item {i}"): [222] for i in range(45)}, claims={2: 222})
    await pings.handle(ev.Released(1, tuple(item(1, 2, f"Item {i}") for i in range(45))))
    await pings.flush()
    assert sink.mentions == [(222,), (), ()]


async def test_ping_modes():
    watched = {(2, "Hookshot"): [222, 333, 444]}
    modes = {333: NotifMode.DM, 444: NotifMode.OFF}
    pings, sink = ping_service(watched, modes)
    await pings.handle(item(1, 2, "Hookshot"))
    await pings.flush()
    assert sink.mentions == [(222,)]
    [dm] = sink.dms
    assert dm.startswith("333: ### 📬 Nouveaux items · Async\n🟣 **Hookshot** pour Bob\\_\\*x\\* · de Alice")
    assert "444" not in "".join(sink.sent + sink.dms)


def chat_feed(address="archipelago.gg:40000"):
    sink = Sink()
    return FeedService(address, lambda e: chat_event(e, STATE, SETTINGS), sink, connections=True), sink


async def test_feed_reports_only_sleep_and_address_changes():
    feed, sink = chat_feed()
    conn = lambda state, port=40000: ev.ConnectionChanged(state, f"archipelago.gg:{port}")  # noqa: E731

    await feed.handle(conn(ev.ConnectionState.RECONNECTING))
    await feed.handle(conn(ev.ConnectionState.UNREACHABLE))
    await feed.handle(conn(ev.ConnectionState.CONNECTED))
    await feed.flush()
    assert sink.sent == []  # same address: nothing worth a message

    await feed.handle(conn(ev.ConnectionState.ASLEEP))
    await feed.handle(conn(ev.ConnectionState.CONNECTED, 41000))
    await feed.flush()
    [message] = sink.sent
    assert "endormie" in message and "nouveau lien · `archipelago.gg:41000`" in message


async def test_the_full_log_says_nothing_about_the_connection():
    sink = Sink()
    feed = FeedService(STATE.address, lambda e: render_event(e, STATE, SETTINGS), sink)
    await feed.handle(ev.ConnectionChanged(ev.ConnectionState.ASLEEP, STATE.address))
    await feed.handle(item())
    await feed.flush()
    assert sink.sent == ["🟣 Alice → **Hookshot** → Bob\\_\\*x\\*\n-# Kakariko Well"]


async def test_sleep_is_told_once_until_something_happens():
    feed, sink = chat_feed(STATE.address)
    conn = lambda state: ev.ConnectionChanged(state, STATE.address)  # noqa: E731
    for _ in range(3):  # the room is woken up and falls asleep again every couple of hours
        await feed.handle(conn(ev.ConnectionState.ASLEEP))
        await feed.handle(conn(ev.ConnectionState.CONNECTED))
    await feed.flush()
    assert len(sink.sent) == 1

    await feed.handle(ev.ChatMessage(1, "hop"))
    await feed.handle(conn(ev.ConnectionState.ASLEEP))
    await feed.flush()
    assert "endormie" in sink.sent[-1]


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
