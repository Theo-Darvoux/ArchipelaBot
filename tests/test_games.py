import io
import zipfile
from datetime import UTC, datetime

import discord
import pytest

from archipelabot.ap.webhost import RoomNotFound, RoomStatus
from archipelabot.core import events as ev
from archipelabot.storage.guilds import GuildConfig
from archipelabot.ui.games import GameManager
from archipelabot.ui.signup_buttons import MyYamlsView, YamlButton, YamlModal, YamlsZipButton

from .ap_server import DEV_DIR, requires_ap_server
from .fakes import FakeAttachment, FakeInteraction, FakeUser, FakeUserMessage, view_text
from .test_track_cog import NotFoundResponse, discord_env, wait_until  # noqa: F401

ROLE = 4242


def yaml(name: str, game: str = "Celeste") -> bytes:
    return f"name: {name}\ngame: {game}\n{game}: {{}}\n".encode()


async def new_game(bot, guild, user=77, **kwargs) -> FakeInteraction:
    cog = bot.get_cog("partie")
    interaction = FakeInteraction(guild=guild, user=FakeUser(id=user))
    await cog.new.callback(cog, interaction, **{"nom": "Samedi", "description": None, **kwargs})
    return interaction


@pytest.fixture
async def game(bot, discord_env):  # noqa: F811
    guild, forum = discord_env
    await bot.guild_configs.save(GuildConfig(guild.id, forum_id=forum.id, ping_role_id=ROLE))
    await new_game(bot, guild, description="21h, vocal")
    [thread] = forum.threads
    return bot.games.by_thread(thread.id), thread


def click(bot, user=77, **kwargs) -> FakeInteraction:
    interaction = FakeInteraction(user=FakeUser(id=user), **kwargs)
    interaction.client = bot
    return interaction


async def test_new_game_opens_a_signup_post(bot, game):
    record, thread = game
    assert thread.name == "Samedi"
    assert [t.name for t in thread.applied_tags] == ["Inscriptions"]
    assert thread.messages[0].pinned
    panel = view_text(thread.messages[0].view)
    assert "Inscriptions ouvertes" in panel and "21h, vocal" in panel and f"<@&{ROLE}>" in panel
    assert "Aucun yaml" in panel
    buttons = [i.item for i in thread.messages[0].view.walk_children() if isinstance(i, discord.ui.DynamicItem)]
    assert [b.label for b in buttons] == ["Mon yaml", "Tous les yamls"]
    assert record.status == "open" and [g.id for g in await bot.games.repo.open()] == [record.id]


async def test_new_game_needs_a_forum(bot, discord_env):  # noqa: F811
    guild, _ = discord_env
    await bot.guild_configs.save(GuildConfig(guild.id))
    with pytest.raises(Exception, match="/config forum"):
        await new_game(bot, guild)


async def test_yamls_fill_the_panel(bot, game):
    record, thread = game
    uploads = await bot.games.upload(
        record,
        77,
        [
            FakeAttachment("psders.yaml", yaml("Psders1", "Luigi's Mansion")),
            FakeAttachment("leo.yml", yaml("Leo{number}") + b"---\n" + yaml("AVeryLongPlayerName", "Terraria")),
            FakeAttachment("notes.txt", b"hello"),
        ],
    )
    assert [u.error for u in uploads] == [None, None, "ce n'est pas un fichier `.yaml`."]
    assert uploads[1].warnings
    panel = view_text(thread.messages[0].view)
    assert "3 slots · 1 joueur" in panel
    assert "**Psders1** · *Luigi's Mansion* · <@77>" in panel
    assert "**Leo{number}** *(nom provisoire)*" in panel
    assert "**AVeryLongPlayerN** · *Terraria*" in panel

    [clash] = await bot.games.upload(record, 5, [FakeAttachment("other.yaml", yaml("psders1"))])
    assert clash.error == "Le slot **Psders1** est déjà pris par <@77>."

    [new] = await bot.games.upload(record, 77, [FakeAttachment("v2.yaml", yaml("Psders1", "Hollow Knight"))])
    assert new.replaced == ["psders.yaml"]
    assert "**Psders1** · *Hollow Knight*" in view_text(thread.messages[0].view)
    assert len(await bot.games.files(record)) == 2


async def test_my_yaml_button_opens_the_upload_then_lists_mine(bot, game):
    record, thread = game
    interaction = click(bot)
    await YamlButton(record.id).callback(interaction)
    modal = interaction.response.sent[0]["modal"]
    assert isinstance(modal, YamlModal)

    modal.upload._values = [FakeAttachment("psders.yaml", yaml("Psders1")), FakeAttachment("bad.yaml", b"[")]
    submit = click(bot)
    await modal.on_submit(submit)
    report = view_text(submit.followup.sent[0]["view"])
    assert "**Psders1** · *Celeste*" in report and "pas un yaml valide" in report

    interaction = click(bot)
    await YamlButton(record.id).callback(interaction)
    view = interaction.response.sent[0]["view"]
    assert isinstance(view, MyYamlsView) and "`psders.yaml`" in view_text(view)
    remove = next(b for b in view.walk_children() if getattr(b, "label", "") == "Retirer")
    await remove.callback(click(bot))
    assert "aucun yaml" in view_text(view)
    assert "Aucun yaml" in view_text(thread.messages[0].view)


async def test_everyone_can_download_every_yaml(bot, game):
    record, _ = game
    interaction = click(bot, user=5)
    await YamlsZipButton(record.id).callback(interaction)
    assert "Personne" in view_text(interaction.response.sent[0]["view"])

    await bot.games.upload(record, 1, [FakeAttachment("player.yaml", yaml("A"))])
    await bot.games.upload(record, 2, [FakeAttachment("player.yaml", yaml("B"))])
    interaction = click(bot, user=5)
    await YamlsZipButton(record.id).callback(interaction)
    [sent] = interaction.response.sent
    assert sent["ephemeral"] and "2 yamls" in view_text(sent["view"])
    archive = zipfile.ZipFile(io.BytesIO(sent["file"].fp.read()))
    assert archive.namelist() == ["player.yaml", "player (2).yaml"]
    assert archive.read("player (2).yaml") == yaml("B")


async def test_yamls_dropped_in_the_post(bot, game):
    record, thread = game
    cog = bot.get_cog("partie")
    good = FakeUserMessage(thread, attachments=[FakeAttachment("a.yaml", yaml("A"))])
    await cog.on_message(good)
    assert good.reactions == ["✅"] and not good.replies

    bad = FakeUserMessage(thread, attachments=[FakeAttachment("b.yaml", b"name: B\n")], author=FakeUser(id=5))
    await cog.on_message(bad)
    assert "Il manque le jeu" in view_text(bad.replies[0]["view"])

    chat = FakeUserMessage(thread, content="salut", attachments=[FakeAttachment("img.png", b"")])
    await cog.on_message(chat)
    assert not chat.reactions and not chat.replies
    assert len(await bot.games.files(record)) == 1


async def test_games_survive_a_restart(bot, game):
    record, thread = game
    bot.games = GameManager(bot)
    await bot.games.restore()
    assert bot.games.by_thread(thread.id).id == record.id
    interaction = click(bot)
    await YamlButton(record.id).callback(interaction)
    assert isinstance(interaction.response.sent[0]["modal"], YamlModal)

    await bot.on_raw_thread_delete(type("Payload", (), {"thread_id": thread.id})())
    assert bot.games.by_thread(thread.id) is None
    interaction = click(bot)
    await YamlButton(record.id).callback(interaction)
    assert "fermées" in view_text(interaction.response.sent[0]["view"])


@requires_ap_server
async def test_tracking_in_the_post_claims_the_slots(bot, game, ap_server):
    record, thread = game
    for user, name in ((1, "p1.yaml"), (2, "p2.yaml")):
        await bot.games.upload(record, user, [FakeAttachment(name, (DEV_DIR / "players" / name).read_bytes())])

    cog = bot.get_cog("track")
    interaction = FakeInteraction(guild=thread.parent.guild, channel_id=thread.id, user=FakeUser(id=9))
    await cog.start.callback(cog, interaction, lien=ap_server.address, slot="Carol", mot_de_passe=None, nom=None)
    runtime = bot.rooms.by_thread(thread.id)
    assert runtime is not None and len(thread.parent.threads) == 1
    assert runtime.record.name == "Samedi" and runtime.record.created_by == 77
    assert "Room suivie" in view_text(interaction.followup.sent[0]["view"])
    assert [t.name for t in thread.applied_tags] == ["En cours"]
    assert runtime.claims == {1: 1, 2: 2}
    assert bot.games.by_thread(thread.id) is None
    assert (await bot.games.repo.open()) == []

    await wait_until(lambda: runtime.tracker.state.connection == ev.ConnectionState.CONNECTED)
    panel = view_text(thread.messages[0].view)
    assert "Connecté" in panel and "Inscriptions" not in panel
    opened = next(m for m in thread.messages[1:] if "La room est ouverte" in m.text)
    assert f"<@&{ROLE}> <@1> <@2>" in opened.text
    assert {u.id for u in opened.allowed_mentions.users} == {1, 2}


@requires_ap_server
async def test_pasting_the_room_link_starts_tracking(bot, game, ap_server, monkeypatch):
    _, thread = game

    class Site:
        async def room_status(self, _room):
            return RoomStatus(ap_server.port, [("Alice", "Celeste 64")], datetime.now(UTC), 7200, None)

        async def current_address(self, _room):
            return ap_server.address

        async def wake(self, _room):
            pass

    monkeypatch.setattr(type(bot), "webhost", property(lambda _self: Site()))
    cog = bot.get_cog("partie")
    message = FakeUserMessage(thread, content="c'est parti : https://archipelago.gg/room/AbC-1 !", author=FakeUser(5))
    await cog.on_message(message)
    assert not message.replies
    runtime = bot.rooms.by_thread(thread.id)
    assert runtime.record.webhost.room_id == "AbC-1"

    again = FakeUserMessage(thread, content="https://archipelago.gg/room/AbC-1")
    await cog.on_message(again)
    assert not again.replies  # the post is a room now: the link is just chat


async def test_a_refused_link_is_explained(bot, game, monkeypatch):
    record, thread = game

    class Site:
        async def room_status(self, _room):
            raise RoomNotFound("room introuvable")

    monkeypatch.setattr(type(bot), "webhost", property(lambda _self: Site()))
    cog = bot.get_cog("partie")
    message = FakeUserMessage(thread, content="https://archipelago.gg/room/nope")
    await cog.on_message(message)
    text = view_text(message.replies[0]["view"])
    assert "Impossible de lire cette room" in text and "/track start" in text
    assert bot.games.by_thread(thread.id) is record


async def test_games_whose_post_was_deleted_offline_are_cancelled(bot, game):
    record, thread = game
    thread.parent.threads.remove(thread)

    async def fetch_channel(_channel_id):
        raise discord.NotFound(NotFoundResponse(), "Unknown Channel")

    bot.fetch_channel = fetch_channel
    bot.games = GameManager(bot)
    await bot.games.restore()
    assert bot.games.get(record.id) is None
    assert await bot.games.repo.open() == []
