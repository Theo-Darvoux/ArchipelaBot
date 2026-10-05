from archipelabot.ap.protocol import ClientStatus, ItemFlags
from archipelabot.core import events as ev
from archipelabot.core.room import RoomState
from archipelabot.ui.services.notify import NotifyService
from archipelabot.ui.services.presence import Presence

from .fakes import view_text
from .test_feed import STATE, item


class DMs:
    def __init__(self, failing: set[int] = frozenset()) -> None:
        self.sent: dict[int, list[str]] = {}
        self.failing = failing

    async def send_dm(self, user_id, view) -> None:
        if user_id in self.failing:
            raise RuntimeError("Cannot send messages to this user")
        self.sent.setdefault(user_id, []).append(view_text(view))


async def test_progression_items_are_grouped_per_user():
    dms = DMs()
    notify = NotifyService(STATE, lambda: "Async", lambda: "https://discord.com/channels/1/2", {2: 222}.get, dms)

    await notify.handle(item(1, 2, "Hookshot"))
    await notify.handle(item(1, 2, "Rupee", ItemFlags.FILLER))  # not progression
    await notify.handle(item(1, 3, "Bow"))  # Carol has no DM target
    await notify.handle(item(2, 2, "Morph Ball"))  # found by Bob himself
    await notify.handle(ev.Released(3, (item(3, 2, "Ice Beam"), item(3, 1, "Lens"))))
    await notify.handle(ev.GoalReached(1))
    await notify.flush()

    [message] = dms.sent[222]
    assert message.startswith("### 📬 Nouveaux items · Async")
    assert "🟣 **Hookshot** de Alice" in message and "**Ice Beam** de Carol" in message
    assert "Rupee" not in message and "Morph Ball" not in message
    assert "[Voir la room](https://discord.com/channels/1/2)" in message

    await notify.flush()
    assert len(dms.sent[222]) == 1


async def test_closed_dms_do_not_block_others():
    dms = DMs(failing={111})
    notify = NotifyService(STATE, lambda: "Async", lambda: None, {1: 111, 2: 222}.get, dms)
    await notify.handle(item(3, 1))
    await notify.handle(item(3, 2))
    await notify.flush()
    assert list(dms.sent) == [222]


async def test_one_dm_per_absence_and_none_while_playing():
    now = [0.0]
    state = RoomState("archipelago.gg:38281", slots=dict(STATE.slots), statuses={2: ClientStatus.PLAYING})
    presence = Presence(state, grace=120, clock=lambda: now[0])
    dms = DMs()
    notify = NotifyService(state, lambda: "Async", lambda: None, {2: 222}.get, dms, presence=presence)

    await notify.handle(item(1, 2, "Hookshot"))
    await notify.flush()
    assert dms.sent == {}

    state.statuses[2] = ClientStatus.UNKNOWN
    await presence.handle(ev.PlayerLeft(2))
    now[0] = 60
    await notify.handle(item(1, 2, "Bow"))
    await notify.handle(item(3, 2, "Lens"))
    await notify.flush()
    assert dms.sent == {}

    now[0] = 130
    await notify.flush()
    [message] = dms.sent[222]
    assert "**Bow**" in message and "**Lens**" in message

    await notify.handle(item(1, 2, "Hammer"))
    now[0] = 500
    await notify.flush()
    assert len(dms.sent[222]) == 1
