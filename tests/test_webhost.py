from datetime import UTC, datetime, timedelta

import aiohttp
import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

from archipelabot.ap.webhost import (
    RoomStatus,
    WebhostClient,
    WebhostError,
    WebhostRoom,
    is_web_link,
    parse_room_url,
)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("https://archipelago.gg/room/AbC-12_x", WebhostRoom("https://archipelago.gg", "AbC-12_x")),
        ("archipelago.gg/room/AbC/", WebhostRoom("https://archipelago.gg", "AbC")),
        ("http://localhost:8080/room/x1", WebhostRoom("http://localhost:8080", "x1")),
        ("archipelago.gg:38281", None),
        ("https://archipelago.gg/seed/AbC", None),
        ("https://archipelago.gg/room/AbC?utm=x#top", WebhostRoom("https://archipelago.gg", "AbC")),
    ],
)
def test_parse_room_url(text, expected):
    assert parse_room_url(text) == expected


def test_web_links_are_told_apart_from_server_addresses():
    assert is_web_link("https://archipelago.gg/tracker/AbC") and is_web_link("archipelago.gg/seed/AbC")
    assert not is_web_link("archipelago.gg:38281") and not is_web_link("ws://localhost:1234/")


def test_room_status_open_or_asleep():
    now = datetime(2026, 10, 4, 20, 0, tzinfo=UTC)
    status = RoomStatus.from_json(
        {"last_port": 38281, "players": [["Alice", "Celeste 64"]], "timeout": 7200, "tracker": "t",
         "last_activity": "Sun, 04 Oct 2026 19:00:00 GMT"}
    )  # fmt: skip
    assert status.players == [("Alice", "Celeste 64")]
    assert status.is_open(now)
    assert not status.is_open(now + timedelta(hours=2))
    assert not RoomStatus.from_json({"last_port": 0, "last_activity": "2026-10-04T19:59:00"}).is_open(now)


@pytest.fixture
async def fake_webhost():
    state = {"port": 40000, "activity": datetime.now(UTC), "page_hits": 0}

    async def room_status(request: web.Request) -> web.Response:
        if request.match_info["room"] != "known":
            return web.Response(status=404, text="<html>")
        return web.json_response(
            {"last_port": state["port"], "players": [["Alice", "Celeste 64"]], "timeout": 7200, "tracker": "t",
             "last_activity": state["activity"].strftime("%a, %d %b %Y %H:%M:%S GMT")}
        )  # fmt: skip

    async def room_page(_request: web.Request) -> web.Response:
        state["page_hits"] += 1
        state["activity"] = datetime.now(UTC)
        return web.Response(text="<html>")

    app = web.Application()
    app.router.add_get("/api/room_status/{room}", room_status)
    app.router.add_get("/room/{room}", room_page)
    async with TestServer(app) as server, aiohttp.ClientSession() as session:
        base = str(server.make_url("")).rstrip("/")
        yield WebhostClient(session), base, state


async def test_current_address_follows_port_and_sleep(fake_webhost):
    client, base, state = fake_webhost
    room = WebhostRoom(base, "known")
    assert await client.current_address(room) == f"127.0.0.1:{state['port']}"

    state["port"] = 40001
    assert await client.current_address(room) == "127.0.0.1:40001"

    state["activity"] -= timedelta(hours=3)
    assert await client.current_address(room) is None
    assert state["page_hits"] == 0  # looking up the status never wakes the room

    await client.wake(room)
    assert state["page_hits"] == 1
    assert await client.current_address(room) == "127.0.0.1:40001"


async def test_unknown_room(fake_webhost):
    client, base, _ = fake_webhost
    with pytest.raises(WebhostError, match="introuvable"):
        await client.room_status(WebhostRoom(base, "nope"))
