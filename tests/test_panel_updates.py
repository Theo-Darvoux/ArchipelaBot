import asyncio

import pytest

from archipelabot.ap import protocol as p
from archipelabot.ap.client import open_session
from archipelabot.ui.emojis import E

from .ap_server import requires_ap_server
from .fakes import view_text
from .test_track_cog import CAROL, discord_env, track  # noqa: F401

pytestmark = requires_ap_server


class CarolLine:
    """Carol's line in each panel edit."""

    def __init__(self, runtime) -> None:
        self.edits: list[str] = []
        self._edit = runtime.panel.edit
        runtime.panel.edit = self.edit
        runtime.panel.min_interval = 0.1

    async def edit(self, view) -> None:
        self.edits.append(next(line for line in view_text(view).splitlines() if "Carol" in line))
        await self._edit(view)

    async def until(self, text: str) -> None:
        async with asyncio.timeout(5):
            while not (self.edits and text in self.edits[-1]):
                await asyncio.sleep(0.05)


@pytest.fixture
async def panel(bot, discord_env, ap_server):  # noqa: F811
    guild, forum = discord_env
    await track(bot, guild, lien=ap_server.address, slot="Alice")
    runtime = bot.rooms.by_thread(forum.threads[0].id)
    return runtime, CarolLine(runtime)


async def test_joins_deaths_and_leaves(panel, ap_server):
    _, carol_line = panel
    carol = await open_session(ap_server.address, CAROL)
    await carol_line.until(E.online)
    await carol.send({"cmd": "Bounce", "tags": ["DeathLink"], "data": p.death_link_data("Carol", "boom", 1.0)})
    await carol_line.until(E.death)
    await carol.close()
    await carol_line.until(E.offline)


async def test_presence_while_the_bot_chats_as_the_player(panel, ap_server):
    runtime, carol_line = panel
    carol = await open_session(ap_server.address, CAROL)
    await carol.send(p.status_update_packet(p.ClientStatus.PLAYING))
    await carol_line.until(E.online)

    await runtime.chat.say(3, "salut")
    await carol.close()  # the chat connection keeps Carol's status up on the server
    await carol_line.until(E.offline)

    carol = await open_session(ap_server.address, CAROL)
    await carol_line.until(E.online)
    await carol.close()
