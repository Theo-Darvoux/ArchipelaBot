import asyncio

import pytest
import websockets

from archipelabot.ap import protocol as p
from archipelabot.ap.client import APConnectionError, APRefused, ConnectOptions, candidate_urls, open_session
from archipelabot.ap.datapackage import DataPackageStore

from .ap_server import APServer, free_port, requires_ap_server

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
async def test_handshake_without_scheme_falls_back_to_ws(ap_server):
    session = await open_session(f"127.0.0.1:{ap_server.port}", BOT)
    assert session.url.startswith("ws://")
    assert {s.name for s in session.connected.slot_info.values()} == {"Alice", "Bob", "Carol"}
    assert session.room_info.datapackage_checksums
    await session.close()


@requires_ap_server
async def test_unknown_slot_is_refused(ap_server):
    with pytest.raises(APRefused) as exc:
        await open_session(ap_server.address, ConnectOptions(slot="Nobody"))
    assert "InvalidSlot" in exc.value.errors


@requires_ap_server
async def test_receives_items_chat_and_deaths_from_other_players(ap_server, tmp_path):
    bot = await open_session(ap_server.address, BOT)
    store = DataPackageStore(tmp_path)
    store.add(await bot.get_data_package(store.missing(bot.room_info.datapackage_checksums)))
    assert store.missing(bot.room_info.datapackage_checksums) == []

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
    assert DataPackageStore(tmp_path).missing(bot.room_info.datapackage_checksums) == []
    await carol.close()
    await bot.close()


@requires_ap_server
async def test_read_only_keys_and_notifications(ap_server):
    bot = await open_session(ap_server.address, BOT)
    keys = [p.client_status_key(0, 3), p.hints_key(0, 3)]
    assert await bot.get(keys) == {keys[0]: p.ClientStatus.UNKNOWN, keys[1]: []}

    await bot.send(p.set_notify_packet(keys))
    carol = await open_session(ap_server.address, CAROL)
    await carol.send(p.status_update_packet(p.ClientStatus.GOAL))

    # The server first marks Carol as connected, then as having reached her goal.
    statuses = []
    await next_matching(
        bot,
        lambda pk: (
            isinstance(pk, p.SetReply)
            and pk.key == keys[0]
            and statuses.append(pk.value) is None
            and pk.value == p.ClientStatus.GOAL
        ),
    )
    assert statuses == [p.ClientStatus.CONNECTED, p.ClientStatus.GOAL]
    await carol.close()
    await bot.close()


@requires_ap_server
async def test_iteration_ends_when_server_stops():
    server = APServer()
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
