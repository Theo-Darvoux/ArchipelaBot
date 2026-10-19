import io
import json
import zipfile

import aiohttp
import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

from archipelabot.ap.webhost import WebhostClient
from archipelabot.core.apworlds import MAX_SIZE, ApworldError, read_apworld
from archipelabot.storage.apworlds import ApworldRepo


def apworld(module: str = "hacknet", game: str | None = "Hacknet", version: str | None = "0.3.1", **files) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        if game is not None:
            manifest = {"game": game, **({"world_version": version} if version else {})}
            archive.writestr(f"{module}/archipelago.json", json.dumps(manifest))
        archive.writestr(f"{module}/__init__.py", files.pop("init", ""))
        for name, content in files.items():
            archive.writestr(name, content)
    return buffer.getvalue()


def test_reads_the_manifest():
    world = read_apworld(apworld(), 7)
    assert (world.game, world.module, world.version, world.uploaded_by) == ("Hacknet", "hacknet", "0.3.1", 7)
    assert world.filename == "hacknet.apworld" and world.label == "hacknet v0.3.1"
    assert read_apworld(apworld(version=None), 7).label == "hacknet"


def test_old_apworlds_without_manifest():
    code = 'class HacknetWorld(World):\n    """Hack."""\n    game: ClassVar[str] = "Hacknet"\n    web = HacknetWeb()\n'
    world = read_apworld(apworld(game=None, init=code, **{"__MACOSX/._hacknet": "x"}), 7)
    assert (world.game, world.version) == ("Hacknet", "")
    with pytest.raises(ApworldError, match="quel jeu"):
        read_apworld(apworld(game=None, init="game = GAME_NAME\n"), 7)


def test_game_from_a_constant_elsewhere():
    init = (
        "from .names import GAME_NAME\nfrom . import names\n\nclass CupheadWorld(World):\n    game = names.GAME_NAME\n"
    )
    files = {
        "cuphead/names.py": 'GAME_NAME: str = "Cuphead"\n',
        "cuphead/items.py": "class X(Item):\n    game = GAME_NAME\n",
    }
    assert read_apworld(apworld("cuphead", game=None, init=init, **files), 7).game == "Cuphead"
    stale = (
        "class W(World):\n    game = 'A (Open World)'\nclass I(Item):\n    game = 'A'\n"
        "class Web(WebWorld):\n    game = 'B'\n"
    )
    assert read_apworld(apworld(game=None, init=stale), 7).game == "A (Open World)"
    with pytest.raises(ApworldError, match="quel jeu"):
        read_apworld(apworld(game=None, init="class I(Item):\n    game = 'A'\nclass L(Location):\n    game = 'B'\n"), 7)
    assert (
        read_apworld(
            apworld(game=None, init="class W(World):\n    game = 'A'\n", **{"hacknet/x.py": "def f(:\n"}), 7
        ).game
        == "A"
    )


def test_manual_worlds_are_named_after_their_game_json():
    init = "class ManualWorld(World):\n    game: str = game_name\n"
    data = {"game": "PokemonTCG", "creator": "sha", "filler_item_name": "x"}
    world = read_apworld(apworld("manual_pokemontcg_sha", game=None, init=init,
                                 **{"manual_pokemontcg_sha/data/game.json": json.dumps(data)}), 7)  # fmt: skip
    assert world.game == "Manual_PokemonTCG_sha"
    old = {"game": "Entei", "player": "worldoflight"}
    files = {"manual_worldoflight_entei/data/game.json": json.dumps(old)}
    assert read_apworld(apworld("manual_worldoflight_entei", game=None, **files), 7).game == "Manual_Entei_worldoflight"


@pytest.mark.parametrize(
    ("content", "error"),
    [
        (b"not a zip", "zip illisible"),
        (apworld(**{"other/__init__.py": ""}), "un seul dossier"),
        (b"x" * (MAX_SIZE + 1), "trop gros"),
    ],
    ids=["not-a-zip", "two-folders", "too-big"],
)
def test_broken_apworlds(content, error):
    with pytest.raises(ApworldError, match=error):
        read_apworld(content, 7)


async def test_library_keeps_one_copy_and_one_world_per_game(db):
    repo = ApworldRepo(db)
    await db.conn.execute("INSERT INTO game (id, guild_id, name, created_by, created_at) VALUES (1, 1, 'g', 1, '')")
    old = await repo.add(1, read_apworld(apworld(version="0.3.0"), 5), apworld(version="0.3.0"))
    new = await repo.add(1, read_apworld(apworld(), 6), apworld())
    assert (await repo.add(1, read_apworld(apworld(), 9), apworld())).id == new.id
    assert (await repo.add(2, read_apworld(apworld(), 9), apworld())).id != new.id
    assert [a.version for a in await repo.library(1, ["Hacknet", "Celeste"])] == ["0.3.1", "0.3.0"]
    assert await repo.library(1, []) == []

    await repo.attach(1, old, 5)
    replaced = await repo.attach(1, new, 6)
    assert [(a.apworld.id, a.user_id) for a in replaced] == [(old.id, 5)]
    assert [(a.apworld.id, a.user_id) for a in await repo.attached(1)] == [(new.id, 6)]
    assert await repo.contents([new.id]) == {new.id: apworld()}
    await repo.detach(1, new.id)
    assert await repo.attached(1) == []


async def test_core_games_from_the_webhost():
    async def checksums(_request: web.Request) -> web.Response:
        return web.json_response({"Archipelago": "a", "Celeste": "b", "Hollow Knight": "c"})

    app = web.Application()
    app.router.add_get("/api/datapackage_checksum", checksums)
    async with TestServer(app) as server, aiohttp.ClientSession() as session:
        games = await WebhostClient(session).games(str(server.make_url("")).rstrip("/"))
    assert games == {"Celeste", "Hollow Knight"}
