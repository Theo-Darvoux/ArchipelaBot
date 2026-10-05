from datetime import UTC, datetime, timedelta

from archipelabot.ap.protocol import ClientStatus, ItemFlags
from archipelabot.core import events as ev
from archipelabot.core.events import ConnectionState
from archipelabot.core.progress import Baseline, Progress
from archipelabot.core.room import RoomState, SlotInfo
from archipelabot.ui.emojis import E
from archipelabot.ui.render.panel import ROSTER_BUDGET, panel_view, roster
from archipelabot.ui.render.status import player_status_view

from .fakes import view_text

T0 = datetime(2026, 10, 4, 20, 0, tzinfo=UTC)


def make_room(players: int = 3) -> tuple[RoomState, Progress]:
    names = ["Alice", "Bob", "Carol"] + [f"Joueur{i}" for i in range(4, players + 1)]
    state = RoomState(
        "archipelago.gg:38281",
        own_slot=1,
        slots={i: SlotInfo(i, names[i - 1], "Super Metroid") for i in range(1, players + 1)},
        connection=ConnectionState.CONNECTED,
    )
    progress = Progress(state, clock=lambda: T0)
    return state, progress


def test_icons_fall_back_to_unicode_until_uploaded():
    assert (E.prog, E.online, E.goal) == ("🟣", "🟢", "🏆")
    assert E.ring(None) == E.ring(0.0) == "◯" and E.ring(1.0) == "●"
    assert E.url("ap_goal") is None


def test_panel_content_and_order():
    state, progress = make_room()
    progress.apply_baseline(
        Baseline({1: set(range(31)), 2: set(range(62)), 3: set(range(100))}, {1: 100, 2: 100, 3: 100},
                 {1: T0 - timedelta(hours=3)})
    )  # fmt: skip
    progress.apply(ev.GoalReached(3))
    progress.apply(ev.Death("Bob", None))
    state.statuses = {1: ClientStatus.CONNECTED, 2: ClientStatus.PLAYING, 3: ClientStatus.GOAL}

    text = view_text(panel_view("Async", state, progress, since=T0, claims={2: 222}))
    assert "**193 / 300** checks · **64 %** · 1/3 goals" in text
    lines = [line for line in text.splitlines() if "%** · " in line and not line.startswith("**")]
    assert lines == [
        "🏆 **100 %** · Carol · *Super Metroid*",
        "◕ **62 %** · Bob · *Super Metroid* · 💀 1 · 🟢 · <@222>",
        # Alice's slot looks connected because of the bot itself: she's shown offline, with her last check.
        f"◔ **31 %** · Alice · *Super Metroid* · ⚫ <t:{int((T0 - timedelta(hours=3)).timestamp())}:R>",
    ]


def test_panel_before_the_first_sync_and_offline():
    state, progress = make_room()
    state.connection = ConnectionState.RECONNECTING
    text = view_text(panel_view("Async", state, progress, since=T0))
    assert "🟠 Reconnexion…" in text and "**0** checks" in text
    assert "◯ **? %** · Alice · *Super Metroid*\n" in text  # no presence while the bot is offline


def test_huge_rooms_fit_in_one_message():
    state, progress = make_room(players=300)
    text = roster(state, progress, {})
    assert len(text) <= ROSTER_BUDGET
    assert text.endswith("autres · `/status joueur:` pour le détail")


def test_player_status_view():
    state, progress = make_room()
    progress.apply_baseline(Baseline({2: {1, 2}}, {2: 8}))
    progress.apply(ev.ItemSent(1, 2, "Ice Beam", "Lieu", ItemFlags.PROGRESSION, 50))
    progress.apply(ev.ItemSent(3, 2, "Wave Beam", "Lieu", ItemFlags.PROGRESSION, 51))
    progress.apply(ev.Death("Bob", "lava"))

    text = view_text(player_status_view(2, state, progress, {2: 222}))
    assert text.startswith("## Bob\n*Super Metroid* · joué par <@222>\n# 25 %\n-# 2 / 8 checks · ⚫ · 💀 1 mort")
    assert text.endswith("🟣 **Wave Beam** de Carol\n🟣 **Ice Beam** de Alice")
