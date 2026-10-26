import pytest

from archipelabot.core.apworlds import Apworld
from archipelabot.core.compat import Problem, check_options, version_conflicts, version_key
from archipelabot.core.yamls import parse_players

from .ap_server import AP_DIR, DEV_DIR, requires_ap_server
from .fakes import FakeAttachment, button_labels, view_text
from .test_apworlds import apworld
from .test_games import click, game  # noqa: F401
from .test_logic import custom_checksfinder
from .test_track_cog import discord_env  # noqa: F401

PLANDO = "bosses, connections, texts"


def hike(options: str = "", requires: str = "") -> bytes:
    return f"name: Bob\ngame: A Short Hike\n{requires}A Short Hike:\n  goal: nap\n{options}".encode()


def test_requires_are_read():
    [player] = parse_players(
        b"name: A\ngame: X\nrequires:\n  version: 0.6.3\n  game:\n    X: 1.2.0\n    Y: {min: 1.0, max: 2.0}\n"
        b"    Z: null\nX: {}\n"
    )
    assert player.requires == (("X", "1.2.0", ""), ("Y", "1.0", "2.0"))
    assert parse_players(b"name: A\ngame: X\nrequires: 0.6\nX: {}\n")[0].requires == ()


def test_versions():
    assert version_key("1.2") == (1, 2, 0) and version_key("v0.3.1") == (0, 3, 1) and version_key("1.0-beta") is None
    hacknet = Apworld("Hacknet", "hacknet", "0.3.1", "", 1)
    players = parse_players(
        b"name: A\ngame: Hacknet\nrequires: {game: {Hacknet: 0.4.0}}\nHacknet: {}\n---\n"
        b"name: B\ngame: Hacknet\nrequires: {game: {Hacknet: {min: 0.1, max: 0.3.0}}}\nHacknet: {}\n---\n"
        b"name: C\ngame: Hacknet\nrequires: {game: {Hacknet: 0.3.1, Other: 9.0}}\nHacknet: {}\n"
    )
    assert [(p.name, bound, version) for p, bound, version, _ in version_conflicts(players, {"Hacknet": hacknet})] == [
        ("A", "min", "0.4.0"),
        ("B", "max", "0.3.0"),
    ]
    assert version_conflicts(players, {"Hacknet": Apworld("Hacknet", "hacknet", "", "", 1)}) == []


async def test_version_conflicts_show_at_upload_and_on_the_panel(bot, game):  # noqa: F811
    record, _ = game
    wants_new = b"name: Ann\ngame: Hacknet\nrequires:\n  game:\n    Hacknet: 0.4.0\nHacknet: {}\n"
    [upload] = await bot.games.upload(record, 1, [FakeAttachment("ann.yaml", wants_new)])
    assert upload.warnings == []  # no apworld yet: nothing to compare
    [upload] = await bot.games.upload(record, 2, [FakeAttachment("hacknet.apworld", apworld())])
    expected = "**Ann** veut *Hacknet* 0.4.0 ou plus, l'apworld de la partie est en v0.3.1"
    assert upload.warnings == [expected]
    assert f"⚠️ {expected}" in view_text(await bot.games.render(record))
    [upload] = await bot.games.upload(record, 3, [FakeAttachment("bea.yaml", wants_new.replace(b"Ann", b"Bea"))])
    assert upload.warnings == [expected.replace("Ann", "Bea")]


@requires_ap_server
async def test_every_option_is_checked_in_archipelago():
    files = [
        ("Players/p1.yaml", (DEV_DIR / "players" / "p1.yaml").read_bytes()),
        ("Players/bob.yaml", hike("  golden_feathers: 50\n  buckets: {1: 5, 9: 0}\n  sticks: {2: 1, 12: 1}\n")),
        ("Players/old.yaml", hike("  coins_everywhere: true\n").replace(b"Bob", b"Old")),
        ("Players/goal.yaml", hike(requires="requires: {game: {Custom ChecksFinder: 2.0.0}}\n").replace(b"Bob", b"G")),
        ("custom_worlds/custom_checksfinder.apworld", custom_checksfinder()),
    ]
    problems = await check_options(AP_DIR / ".venv" / "bin" / "python", AP_DIR, files, PLANDO)
    by_player = {}
    for problem in problems:
        by_player.setdefault(problem.player, []).append(problem)
    assert set(by_player) == {"Bob", "Old", "G"}
    feathers, sticks = by_player["Bob"]
    assert (feathers.option, feathers.value, feathers.error) == ("golden_feathers", "50", True)
    assert "50" in feathers.detail
    assert (sticks.option, sticks.value) == ("sticks", "12")  # 9 buckets has no weight: never rolled
    assert by_player["Old"] == [Problem("old.yaml", "Old", "A Short Hike", "warning", "coins_everywhere", None, "")]
    [version] = by_player["G"]
    assert version.error and version.option is None and "at least 2.0.0" in version.detail


@requires_ap_server
async def test_only_the_host_runs_the_check(bot, game):  # noqa: F811
    from archipelabot.ui.signup_buttons import CheckButton

    record, _ = game
    bot.settings.archipelago_path = AP_DIR
    assert "Vérifier" in button_labels(await bot.games.render(record))
    await bot.games.upload(record, 2, [FakeAttachment("bob.yaml", hike("  golden_feathers: 50\n"))])
    button = CheckButton(record.id)

    stranger = click(bot, user=5)
    await button.callback(stranger)
    assert "exécute le code des apworlds" in view_text(stranger.response.sent[0]["view"])

    host = click(bot, user=77)
    await button.callback(host)
    text = view_text(host.followup.sent[0]["view"])
    assert "1 erreur · la génération échouera" in text and "`golden_feathers: 50` refusé" in text

    await bot.games.upload(record, 2, [FakeAttachment("bob.yaml", hike())])
    host = click(bot, user=77)
    await button.callback(host)
    assert "Tous les yamls passent" in view_text(host.followup.sent[0]["view"])


@pytest.mark.parametrize("version", ["0.6.8"])
def test_reports_name_the_archipelago_version(version):
    from archipelabot.ui.signup_buttons import check_report

    warning = Problem("old.yaml", "Old", "A Short Hike", "warning", "coins_everywhere")
    text = view_text(check_report(version, [warning]))
    assert (
        "1 option inconnue" in text
        and "option `coins_everywhere` inconnue" in text
        and f"Archipelago {version}" in text
    )
