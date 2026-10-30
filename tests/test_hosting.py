import dataclasses
import socket

import pytest

from archipelabot.ap import protocol as p
from archipelabot.ap.client import ConnectOptions, open_session
from archipelabot.core import events as ev
from archipelabot.core.hosting import (
    HostedServer,
    Hosting,
    HostingError,
    ServerOptions,
    parse_ports,
    pick_port,
    slot_files,
)
from archipelabot.errors import UserError
from archipelabot.storage.rooms import RoomRecord, RoomRepo
from archipelabot.ui.hosting import HostManager
from archipelabot.ui.manage import LaunchButton
from archipelabot.ui.panel_buttons import PatchButton
from archipelabot.ui.rooms import RoomManager

from .ap_server import AP_DIR, DEV_DIR, free_port, requires_ap_server
from .fakes import FakeAttachment, FakeInteraction, button_labels, view_text
from .test_games import click, game  # noqa: F401
from .test_track_cog import discord_env, wait_until  # noqa: F401

CAROL = ConnectOptions(slot="Carol", game="ChecksFinder", tags=(), items_handling=0b111, uuid="carol")


def test_ports():
    assert parse_ports("38281-38283") == range(38281, 38284)
    assert parse_ports(" 5000 ") == range(5000, 5001)
    for wrong in ("x", "10-5", "0", "70000"):
        with pytest.raises(ValueError):
            parse_ports(wrong)

    port = free_port()
    with socket.socket() as busy:
        busy.bind(("", port))
        with pytest.raises(HostingError, match="occupés"):
            pick_port(range(port, port + 1), [])
    assert pick_port(range(port, port + 1), []) == port
    with pytest.raises(HostingError):
        pick_port(range(port, port + 1), [port])


def test_server_options_become_arguments():
    assert ServerOptions("goal", "disabled", "enabled", 0).args() == [
        "--release_mode", "goal", "--collect_mode", "disabled", "--remaining_mode", "enabled", "--hint_cost", "0",
    ]  # fmt: skip


def test_files_are_sorted_by_slot(tmp_path):
    for name in ("AP_123.archipelago", "AP_123_Spoiler.txt", "AP_123_P1_Alice.apz5", "AP_123_P12_Bo_b.apmc",
                 "AP_123_P1_Alice.zip", "AP_123.apsave", "server.log"):  # fmt: skip
        (tmp_path / name).write_bytes(b"")
    files = slot_files(tmp_path)
    assert {slot: [f.name for f in paths] for slot, paths in files.items()} == {
        1: ["AP_123_P1_Alice.apz5", "AP_123_P1_Alice.zip"],
        12: ["AP_123_P12_Bo_b.apmc"],
    }
    assert slot_files(tmp_path / "gone") == {}


async def test_hosting_is_stored(db):
    repo = RoomRepo(db)
    hosting = Hosting("123", 38281, "0.6.8", ServerOptions("goal", "goal", "enabled", 5))
    record = await repo.create(RoomRecord(1, "Room", "play.example:38281", "Alice", 7, hosting=hosting))
    await repo.create(RoomRecord(1, "Other", "x:1", "Bob", 7))
    assert (await repo.get(record.id)).hosting == hosting
    assert await repo.hosted_ports() == [38281]
    await repo.set_status(record, "finished")
    assert await repo.hosted_ports() == []


async def launch_modal(bot, record, user=77):
    interaction = click(bot, user=user)
    await LaunchButton(record.id).callback(interaction)
    return interaction.response.sent[0]["modal" if user == 77 else "view"]


async def test_hosting_needs_an_address(bot, game):  # noqa: F811
    record, thread = game
    panel = await bot.games.render(record)
    assert "Lancer" not in button_labels(panel) and "`/track start` ici avec le lien" in view_text(panel)
    with pytest.raises(UserError, match="HOST_ADDRESS"):
        await launch_modal(bot, record)

    bot.settings.host_address = "play.example"
    panel = await bot.games.render(record)
    assert button_labels(panel) == ["Mon yaml", "Tous les yamls", "Lancer", "Gérer"]
    text = view_text(panel)
    assert "**2.** Quand tout le monde est inscrit, l'organisateur clique sur **🖥️ Lancer**" in text
    assert "**3.**" not in text and "Partie générée et hébergée ailleurs ?" in text
    assert "Seule la personne" in view_text(await launch_modal(bot, record, user=5))

    modal = await launch_modal(bot, record)
    modal.to_components()
    assert [o.value for o in modal.release.options if o.default] == ["auto"]
    modal.hint_cost._value = "150"
    with pytest.raises(UserError, match="de 0 à 100"):
        await modal.on_submit(click(bot, guild=thread.parent.guild))
    modal.hint_cost._value = "10"
    with pytest.raises(UserError, match="Personne n'a encore envoyé"):
        await modal.on_submit(click(bot, guild=thread.parent.guild))


@pytest.fixture
def hosting_bot(bot, tmp_path):
    port = free_port()
    bot.settings.database_path = tmp_path / "archipelabot.db"
    bot.settings.archipelago_path = AP_DIR
    bot.settings.host_address = "play.example"
    bot.settings.host_ports = f"{port}-{port + 20}"
    return bot


async def host(bot, thread, cout_hint=10, mot_de_passe="", release="auto") -> FakeInteraction:
    modal = await launch_modal(bot, bot.games.by_thread(thread.id))
    for select in (modal.release, modal.collect, modal.remaining):
        select._values = [next(o.value for o in select.options if o.default)]
    modal.release._values = [release]
    modal.hint_cost._value = str(cout_hint)
    modal.password._value = mot_de_passe
    interaction = click(bot, guild=thread.parent.guild)
    await modal.on_submit(interaction)
    return interaction


@requires_ap_server
async def test_a_hosted_game(hosting_bot, game):  # noqa: F811
    bot = hosting_bot
    record, thread = game
    for user, name in ((1, "p1.yaml"), (2, "p2.yaml"), (3, "p3.yaml")):
        await bot.games.upload(record, user, [FakeAttachment(name, (DEV_DIR / "players" / name).read_bytes())])

    interaction = await host(bot, thread, cout_hint=5, mot_de_passe="secret")
    runtime = bot.rooms.by_thread(thread.id)
    hosting = runtime.record.hosting
    address = f"play.example:{hosting.port}"
    assert f"Pour jouer : `{address}`" in view_text(interaction.followup.sent[0]["view"])
    assert hosting.options == ServerOptions(hint_cost=5) and runtime.record.password == "secret"
    assert bot.games.by_thread(thread.id) is None and [t.name for t in thread.applied_tags] == ["En cours"]
    assert runtime.claims == {1: 1, 2: 2, 3: 3}
    await wait_until(lambda: runtime.tracker.state.connection == ev.ConnectionState.CONNECTED)
    assert address in view_text(runtime.render_panel())
    assert set(runtime.logic.results) == {1, 2, 3} and runtime.record.logic.seed
    assert "en logique" in view_text(runtime.render_panel())
    assert any(f"Pour jouer : `{address}` · avec le mot de passe" in text for text in thread.texts())
    folder = bot.hosting.folder(hosting)
    assert len(list(folder.glob("*.archipelago"))) == 1

    # No game of the dev seed has a patch: the button only shows with one.
    assert "Je joue" in button_labels(runtime.render_panel())
    assert not runtime.has_patches and "Mes patchs" not in button_labels(runtime.render_panel())
    seed = runtime.tracker.state.seed_name
    (folder / f"AP_{seed}_P3_Carol.apcf").write_bytes(b"patch")
    button = PatchButton(runtime.record.id)
    mine = click(bot, user=3)
    await button.callback(mine)
    [sent] = mine.response.sent
    assert [f.filename for f in sent["files"]] == [f"AP_{seed}_P3_Carol.apcf"] and sent["ephemeral"]
    other = click(bot, user=99)
    await button.callback(other)
    picker = other.response.sent[0]["view"]
    assert [o.value for o in picker.select.options] == ["3"]
    alice = click(bot, user=1)
    await button.callback(alice)
    assert "Pas de patch pour Alice" in view_text(alice.response.sent[0]["view"])

    carol = await open_session(hosting.local_address, dataclasses.replace(CAROL, password="secret"))
    checked = carol.connected.missing_locations[:2]
    await carol.send(p.location_checks_packet(checked))
    await wait_until(lambda: runtime.progress[3].done == 2)
    await carol.close()

    # After a restart, the server starts again from its save.
    await bot.rooms.shutdown()
    await bot.hosting.shutdown()
    bot.hosting = HostManager(bot)
    bot.rooms = RoomManager(bot)
    await bot.rooms.restore()
    runtime = bot.rooms.by_thread(thread.id)
    await wait_until(lambda: runtime.tracker.state.connection == ev.ConnectionState.CONNECTED, 60)
    assert "Mes patchs" in button_labels(runtime.render_panel())  # the patch written above
    carol = await open_session(hosting.local_address, dataclasses.replace(CAROL, password="secret"))
    assert set(checked) <= set(carol.connected.checked_locations)
    await carol.close()
    await wait_until(lambda: len(runtime.logic.results) == 3, 60)

    # The server crashing is started again.
    server = bot.hosting.servers[runtime.record.id]
    server.restart_delay = 0.1
    server.process.kill()
    await wait_until(lambda: not server.running)
    await wait_until(lambda: server.running, 60)

    await bot.rooms.stop(runtime, "stopped")
    assert runtime.record.id not in bot.hosting.servers and not server.running
    assert folder.exists()


@requires_ap_server
async def test_back_to_signups_deletes_the_seed(hosting_bot, game):  # noqa: F811
    bot = hosting_bot
    record, thread = game
    await bot.games.upload(record, 3, [FakeAttachment("p3.yaml", (DEV_DIR / "players" / "p3.yaml").read_bytes())])
    await host(bot, thread)
    runtime = bot.rooms.by_thread(thread.id)
    folder = bot.hosting.folder(runtime.record.hosting)
    server = bot.hosting.servers[runtime.record.id]
    assert await bot.rooms.discard(runtime)
    assert not folder.exists() and not server.running


@requires_ap_server
async def test_generation_errors_leave_the_game_open(hosting_bot, game):  # noqa: F811
    bot = hosting_bot
    record, thread = game
    await bot.games.upload(record, 3, [FakeAttachment("p.yaml", b"name: Zed\ngame: ChecksFinder\nChecksFinder: 3\n")])
    with pytest.raises(UserError, match="ne passent pas avec les apworlds") as error:
        await host(bot, thread)
    assert "**Zed**" in str(error.value)
    assert bot.games.by_thread(thread.id) is not None and bot.rooms.by_thread(thread.id) is None
    assert not any(bot.hosting.root.iterdir()) if bot.hosting.root.exists() else True


@requires_ap_server
async def test_a_server_that_cannot_start_is_reported(tmp_path):
    (tmp_path / "AP_1.archipelago").write_bytes(b"not a seed")
    hosting = Hosting("1", free_port(), "0.6.8")
    server = HostedServer(AP_DIR / ".venv" / "bin" / "python", AP_DIR, tmp_path, hosting)
    with pytest.raises(HostingError, match="s'est arrêté au démarrage"):
        await server.start()
    assert not server.running
