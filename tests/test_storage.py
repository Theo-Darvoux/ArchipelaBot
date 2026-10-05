from datetime import UTC, datetime, timedelta

from archipelabot.ap.protocol import ItemFlags
from archipelabot.ap.webhost import WebhostRoom
from archipelabot.storage.claims import ClaimRepo, NotifMode, NotifPrefs
from archipelabot.storage.db import MIGRATIONS, Database
from archipelabot.storage.guilds import GuildConfig, GuildRepo
from archipelabot.storage.history import HistoryRepo, LoggedEvent, Snapshot
from archipelabot.storage.rooms import RoomRecord, RoomRepo, RoomSettings


async def test_migrations_apply_once(tmp_path):
    path = tmp_path / "sub" / "bot.db"
    db = await Database.open(path)
    await db.close()

    # Reopening must not re-run migrations (they would fail on existing tables).
    db = await Database.open(path)
    async with db.conn.execute("PRAGMA user_version") as cur:
        assert (await cur.fetchone())[0] == len(MIGRATIONS)
    await db.close()


async def test_guild_config_defaults_then_roundtrip(db):
    repo = GuildRepo(db)
    assert await repo.get(1) == GuildConfig(1)

    await repo.save(GuildConfig(1, forum_id=10))
    await repo.save(GuildConfig(1, forum_id=11, recap_channel_id=20))
    assert await repo.get(1) == GuildConfig(1, forum_id=11, recap_channel_id=20)
    assert await repo.get(2) == GuildConfig(2)


async def test_room_roundtrip_and_status(db):
    repo = RoomRepo(db)
    room = await repo.create(
        RoomRecord(
            guild_id=1, name="Async", address="archipelago.gg:38281", slot="Alice", created_by=9,
            webhost=WebhostRoom("https://archipelago.gg", "abc"), thread_id=50, panel_message_id=51,
        )
    )  # fmt: skip
    assert room.id is not None

    room.settings.show_filler = True
    await repo.save_settings(room)
    room.address = "archipelago.gg:40000"
    await repo.save_address(room)

    loaded = await repo.get(room.id)
    assert loaded == room
    assert [r.id for r in await repo.active()] == [room.id]

    await repo.set_status(room, "finished")
    assert await repo.active() == []
    assert (await repo.get(room.id)).status == "finished"


def test_room_settings_item_filter():
    settings = RoomSettings()
    assert settings.shows_item(ItemFlags.PROGRESSION | ItemFlags.TRAP)
    assert settings.shows_item(ItemFlags.USEFUL)
    assert settings.shows_item(ItemFlags.TRAP)
    assert not settings.shows_item(ItemFlags.FILLER)


async def test_claims_and_previous_owners(db):
    rooms, claims = RoomRepo(db), ClaimRepo(db)
    old = await rooms.create(RoomRecord(guild_id=1, name="Old", address="a:1", slot="Alice", created_by=9))
    other_guild = await rooms.create(RoomRecord(guild_id=2, name="X", address="a:2", slot="Alice", created_by=9))
    new = await rooms.create(RoomRecord(guild_id=1, name="New", address="a:3", slot="Alice", created_by=9))

    await claims.set(old.id, 1, "Alice", 100)
    await claims.set(old.id, 2, "Bob", 200)
    await claims.set(old.id, 2, "Bob", 201)  # reclaimed by someone else
    await claims.set(other_guild.id, 3, "Carol", 300)
    assert await claims.for_room(old.id) == {1: 100, 2: 201}

    assert await claims.previous(1, ["Alice", "Bob", "Carol", "Dave"]) == {"Alice": 100, "Bob": 201}
    assert await claims.previous(1, []) == {}

    await claims.remove(old.id, 1)
    assert await claims.for_room(old.id) == {2: 201}
    assert await claims.for_room(new.id) == {}


async def test_notif_prefs(db):
    prefs = NotifPrefs(db)
    await prefs.load()
    assert prefs.mode(1) == NotifMode.THREAD
    await prefs.set(1, NotifMode.DM)
    await prefs.set(1, NotifMode.OFF)

    reloaded = NotifPrefs(db)
    await reloaded.load()
    assert reloaded.mode(1) == NotifMode.OFF


async def test_history(db):
    room = await RoomRepo(db).create(RoomRecord(guild_id=1, name="R", address="a:1", slot="Alice", created_by=9))
    history = HistoryRepo(db)
    t0 = datetime(2026, 10, 4, 20, 0, tzinfo=UTC)
    await history.log(room.id, LoggedEvent(t0 + timedelta(minutes=1), "goal", 2))
    await history.log(room.id, LoggedEvent(t0, "death", 1, {"cause": "lava"}))
    assert await history.events(room.id) == [
        LoggedEvent(t0, "death", 1, {"cause": "lava"}),
        LoggedEvent(t0 + timedelta(minutes=1), "goal", 2),
    ]

    snaps = [Snapshot(t0, 1, 3, 10), Snapshot(t0, 2, 0, None)]
    await history.snapshot(room.id, snaps)
    assert await history.snapshots(room.id) == snaps
