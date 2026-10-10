import asyncio

import pytest
import websockets

from archipelabot.ap import protocol as p
from archipelabot.ap.client import APConnectionError, ConnectOptions, candidate_urls, open_session
from archipelabot.ap.datapackage import DataPackageStore

from .ap_server import free_port, fresh_server, requires_ap_server

BOT = ConnectOptions(slot="Alice", tags=("TextOnly", "DeathLink"))
CAROL = ConnectOptions(slot="Carol", game="ChecksFinder", tags=(), items_handling=0b111, uuid="carol")


@pytest.mark.parametrize(
    ("address", "expected"),
    [
        ("archipelago.gg:38281", ["wss://archipelago.gg:38281", "ws://archipelago.gg:38281"]),
        ("localhost", ["wss://localhost:38281", "ws://localhost:38281"]),
        (" ws://10.0.0.1:1234/ ", ["ws://10.0.0.1:1234"]),
    ],
)
def test_candidate_urls(address, expected):
    assert candidate_urls(address) == expected


async def next_matching(session, predicate, wait=5.0):
    async def find():
        async for packet in session:
            if predicate(packet):
                return packet
        raise AssertionError("session closed before the expected packet")

    return await asyncio.wait_for(find(), wait)


async def test_unreachable_server_raises():
    with pytest.raises(APConnectionError):
        await open_session(f"127.0.0.1:{free_port()}", BOT)


@requires_ap_server
async def test_receives_items_chat_and_deaths_from_other_players(ap_server, tmp_path):
    bot = await open_session(ap_server.address, BOT)
    store = DataPackageStore(tmp_path)
    await store.add(await bot.get_data_package(await store.missing(bot.room_info.datapackage_checksums)))
    assert await store.missing(bot.room_info.datapackage_checksums) == []

    carol = await open_session(ap_server.address, CAROL)
    location = carol.connected.missing_locations[0]
    await carol.send(
        p.location_checks_packet([location]),
        p.say_packet("salut"),
        {"cmd": "Bounce", "tags": ["DeathLink"], "data": p.death_link_data("Carol", "Carol a explosé", 1.0)},
    )

    death = await next_matching(bot, lambda pk: isinstance(pk, p.Bounced))
    assert death.data["cause"] == "Carol a explosé"

    sent = await next_matching(bot, lambda pk: isinstance(pk, p.PrintJSON) and pk.type == "ItemSend")
    assert sent.item.location == location and sent.item.player == 3
    checksum = bot.room_info.datapackage_checksums["ChecksFinder"]
    assert not store.location_name("ChecksFinder", checksum, location).startswith("Lieu #")

    chat = await next_matching(bot, lambda pk: isinstance(pk, p.PrintJSON) and pk.type == "Chat")
    assert (chat.slot, chat.message) == (3, "salut")

    # A fresh store finds everything in the disk cache.
    assert await DataPackageStore(tmp_path).missing(bot.room_info.datapackage_checksums) == []
    await carol.close()
    await bot.close()


@requires_ap_server
async def test_iteration_ends_when_server_stops():
    server = fresh_server()
    bot = await open_session(server.address, BOT)
    server.stop()

    async def drain():
        async for _ in bot:
            pass

    await asyncio.wait_for(drain(), 10)
    assert bot.closed
    with pytest.raises(APConnectionError):
        await bot.get(["_read_race_mode"])


async def test_bad_addresses_and_servers_raise_connection_errors(monkeypatch):
    with pytest.raises(APConnectionError):
        await open_session("https://archipelago.gg/tracker/AbC", BOT)

    async def silent(ws):
        await ws.wait_closed()

    async def broken(ws):
        await ws.send('[{"cmd": "RoomInfo"}]')
        await ws.wait_closed()

    monkeypatch.setattr("archipelabot.ap.client.HANDSHAKE_TIMEOUT", 0.5)
    for handler, message in ((silent, "no answer"), (broken, "invalid handshake")):
        async with websockets.serve(handler, "127.0.0.1", 0) as server:
            port = server.sockets[0].getsockname()[1]
            with pytest.raises(APConnectionError, match=message):
                await open_session(f"ws://127.0.0.1:{port}", BOT)
