import io
import random
import re
import zipfile
from datetime import datetime
from zoneinfo import ZoneInfo

import discord
import pytest

from archipelabot.core import events as ev
from archipelabot.core.apworlds import Apworld, Attached
from archipelabot.core.yamls import PlayerYaml, YamlFile
from archipelabot.errors import UserError
from archipelabot.storage.games import GameRecord
from archipelabot.storage.guilds import GuildConfig
from archipelabot.ui.emojis import FALLBACKS, E, uploaded_name
from archipelabot.ui.games import ZIP_OVERHEAD, GameManager
from archipelabot.ui.manage import GameManageButton
from archipelabot.ui.render.signup import signup_view
from archipelabot.ui.signup_buttons import MyYamlsView, UploadReport, YamlButton, YamlModal, YamlsZipButton

from .ap_server import DEV_DIR, requires_ap_server
from .fakes import FakeAttachment, FakeInteraction, FakeUser, FakeUserMessage, view_text
from .test_apworlds import apworld
from .test_track_cog import NotFoundResponse, discord_env, room_manage, wait_until  # noqa: F401

ROLE = 4242


def yaml(name: str, game: str = "Celeste") -> bytes:
    return f"name: {name}\ngame: {game}\n{game}: {{}}\n".encode()


async def new_game(bot, guild, user=77, **kwargs) -> FakeInteraction:
    cog = bot.get_cog("partie")
    interaction = FakeInteraction(guild=guild, user=FakeUser(id=user))
    await cog.new.callback(cog, interaction, **{"nom": "Samedi", "debut": None, "description": None, **kwargs})
    return interaction


@pytest.fixture
async def game(bot, discord_env):  # noqa: F811
    guild, forum = discord_env
    await bot.guild_configs.save(GuildConfig(guild.id, forum_id=forum.id, ping_role_id=ROLE))
    await new_game(bot, guild, debut="12/10/2099 21h", description="Vocal ouvert\npas de spoil")
    [thread] = forum.threads
    return bot.games.by_thread(thread.id), thread


def click(bot, user=77, **kwargs) -> FakeInteraction:
    interaction = FakeInteraction(user=FakeUser(id=user), **kwargs)
    interaction.client = bot
    return interaction


async def game_manage(bot, record, user=77):
    """What the sign-up post's "Gérer" button opens for this user."""
    interaction = click(bot, user=user)
    await GameManageButton(record.id).callback(interaction)
    return interaction.response.sent[0]["view"]


async def test_new_game_opens_a_signup_post(bot, game):
    record, thread = game
    assert thread.name == "Samedi"
    assert [t.name for t in thread.applied_tags] == ["Inscriptions"]
    assert thread.messages[0].pinned
    panel = view_text(thread.messages[0].view)
    assert "> Vocal ouvert\n> pas de spoil" in panel
    starts = int(datetime(2099, 10, 12, 21, tzinfo=ZoneInfo("Europe/Paris")).timestamp())
    assert f"**<t:{starts}:F>** · <t:{starts}:R>" in panel
    assert f"Organisée par <@77> · pour <@&{ROLE}>" in panel
    assert "Inscrits\n*Personne pour l'instant.*" in panel
    assert "Comment participer" in panel and "`/track start` ici avec le lien de la room" in panel
    buttons = [i.item for i in thread.messages[0].view.walk_children() if isinstance(i, discord.ui.DynamicItem)]
    assert [b.label for b in buttons] == ["Mon yaml", "Tous les yamls", "Gérer"]
    assert "Pour l'organisateur et les modérateurs · ⚙️ **Gérer**" in panel
    assert record.status == "open" and [g.id for g in await bot.games.repo.open()] == [record.id]


async def test_new_game_needs_a_forum(bot, discord_env):  # noqa: F811
    guild, _ = discord_env
    await bot.guild_configs.save(GuildConfig(guild.id))
    with pytest.raises(Exception, match="/config"):
        await new_game(bot, guild)


async def test_creation_can_be_restricted_to_admins(bot, discord_env):  # noqa: F811
    guild, forum = discord_env
    await bot.guild_configs.save(GuildConfig(guild.id, forum_id=forum.id, admin_only_games=True))
    with pytest.raises(UserError, match="seuls les admins"):
        await new_game(bot, guild)
    track = bot.get_cog("track")
    with pytest.raises(UserError, match="seuls les admins"):
        await track.start.callback(track, FakeInteraction(guild=guild), lien="localhost:38281", slot="Carol")
    assert forum.threads == []

    cog = bot.get_cog("partie")
    admin = FakeInteraction(guild=guild, permissions=discord.Permissions(manage_guild=True))
    await cog.new.callback(cog, admin, nom="Samedi", debut=None, description=None)
    assert len(forum.threads) == 1


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
    assert [u.error for u in uploads] == [None, None, "ce n'est ni un `.yaml` ni un `.apworld`."]
    assert uploads[1].warnings
    panel = view_text(thread.messages[0].view)
    assert "Inscrits · 1 joueur · 3 slots" in panel
    assert "**Psders1** · *Luigi's Mansion* · <@77>" in panel
    assert "**Leo{number}** *(nom provisoire)*" in panel
    assert "**AVeryLongPlayerN** · *Terraria*" in panel

    [clash] = await bot.games.upload(record, 5, [FakeAttachment("other.yaml", yaml("psders1"))])
    assert clash.error == "Le slot **Psders1** est déjà pris par <@77>."

    [new] = await bot.games.upload(record, 77, [FakeAttachment("v2.yaml", yaml("Psders1", "Hollow Knight"))])
    assert new.replaced == ["psders.yaml"]
    assert "**Psders1** · *Hollow Knight*" in view_text(thread.messages[0].view)
    assert len(await bot.games.files(record)) == 2


def test_a_full_panel_fits_in_one_message():
    user = 10**18
    game = GameRecord(guild_id=1, name="HolidAP", description="Prenez vos gros jeux", created_by=user, id=1)
    games = [f"Some Long Custom Game Name {i}" for i in range(12)]
    apworlds = [Attached(Apworld(name, "m", "1.0.0", "", user), user) for name in games]

    def panel(players: int) -> str:
        files = [YamlFile(user, f"{i}.yaml", b"", [PlayerYaml(f"Player{i:02}", games[i % 12])]) for i in range(players)]
        return view_text(signup_view(game, files, apworlds=apworlds, track_command=f"</track start:{user}>"))

    # Uploaded icons are long custom emoji tags: render with those.
    E.use({name: discord.PartialEmoji(name=uploaded_name(name), id=user) for name in FALLBACKS})
    try:
        everyone, crowd = panel(30), panel(80)
    finally:
        E.reset()
    assert len(everyone) <= 4000 and "Player29" in everyone and "- **Player00**" in everyone
    assert len(crowd) <= 4000 and re.search(r"… et \d+ autres\n", crowd)


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
    assert "**Psders1** · *Celeste*" in report and "**bad.yaml** : ⚠️ Yaml invalide ligne 2" in report
    assert "gardé quand même" in report
    assert "⚠️ `bad.yaml` · *yaml invalide* · <@77>" in view_text(thread.messages[0].view)

    interaction = click(bot)
    await YamlButton(record.id).callback(interaction)
    view = interaction.response.sent[0]["view"]
    assert isinstance(view, MyYamlsView) and "`psders.yaml`" in view_text(view)
    assert "Yaml invalide ligne 2" in view_text(view)

    download = next(b for b in view.walk_children() if getattr(b, "label", "") == "Télécharger mes yamls")
    interaction = click(bot)
    await download.callback(interaction)
    [sent] = interaction.response.sent
    assert sent["ephemeral"] and [f.filename for f in sent["files"]] == ["psders.yaml", "bad.yaml"]
    assert sent["files"][0].fp.read() == yaml("Psders1") and sent["files"][1].fp.read() == b"["

    for _ in range(2):
        remove = next(b for b in view.walk_children() if getattr(b, "label", "") == "Retirer")
        await remove.callback(click(bot))
    assert "aucun yaml" in view_text(view)
    assert not any(getattr(b, "label", "") == "Télécharger mes yamls" for b in view.walk_children())
    assert "Personne pour l'instant" in view_text(thread.messages[0].view)


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
    assert sent["ephemeral"] and "2 yamls" in view_text(sent["view"]) and "apworld" not in view_text(sent["view"])
    archive = zipfile.ZipFile(io.BytesIO(sent["file"].fp.read()))
    assert archive.namelist() == ["Players/player.yaml", "Players/player (2).yaml"]
    assert archive.read("Players/player (2).yaml") == yaml("B")

    await bot.games.upload(record, 3, [FakeAttachment("broken.yaml", b"name: C\ngame: [\n")])
    interaction = click(bot, user=5)
    await YamlsZipButton(record.id).callback(interaction)
    assert "⚠️ Yaml invalide : `broken.yaml` (<@3>)" in view_text(interaction.response.sent[0]["view"])


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
    assert f"Pour jouer : `{ap_server.address}`" in opened.text and "Room :" not in opened.text
    assert "<@1> <@2>" in opened.text and f"<@&{ROLE}>" not in opened.text
    assert {u.id for u in opened.allowed_mentions.users} == {1, 2}
    assert opened.allowed_mentions.roles is False


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


async def test_links_in_the_post_do_not_start_tracking(bot, game):
    record, thread = game
    message = FakeUserMessage(thread, content="https://archipelago.gg/room/AbC-1")
    await bot.get_cog("partie").on_message(message)
    assert not message.replies and bot.games.by_thread(thread.id) is record


async def test_editing_the_game(bot, game):
    record, thread = game
    assert "Seule la personne" in view_text(await game_manage(bot, record, user=5))

    async def edit(**fields):
        interaction = click(bot)
        await buttons_of(await game_manage(bot, record))["Modifier"].callback(interaction)
        modal = interaction.response.sent[0]["modal"]
        for name, value in fields.items():
            getattr(modal, name)._value = value
        await modal.on_submit(click(bot))
        return modal

    modal = await edit()  # the dates it shows read back as the same date
    assert modal.start.default == "lundi 12/10/2099 à 21:00"
    assert modal.description.default == "Vocal ouvert\npas de spoil"
    assert record.starts_at == datetime(2099, 10, 12, 19, tzinfo=ZoneInfo("UTC"))
    with pytest.raises(UserError, match="Précise l'heure"):
        await edit(start="samedi")

    await edit(name="Dimanche", start="")
    assert thread.name == "Dimanche" and record.starts_at is None
    panel = view_text(thread.messages[0].view)
    assert "## Dimanche" in panel and ":F>" not in panel and "> Vocal ouvert" in panel
    [stored] = await bot.games.repo.open()
    assert stored.name == "Dimanche" and stored.starts_at is None

    await edit(start="01/01/2099 20h", description="")
    assert record.starts_at == datetime(2099, 1, 1, 19, tzinfo=ZoneInfo("UTC")) and record.description == ""
    assert (await bot.games.repo.open())[0].starts_at == record.starts_at


async def test_start_suggestions(bot, game):
    cog = bot.get_cog("partie")
    [choice] = await cog.start_autocomplete(FakeInteraction(), "12/10/2099 21h")
    assert choice.name == choice.value == "lundi 12/10/2099 à 21:00"
    with pytest.raises(UserError, match="déjà passée"):
        await new_game(bot, game[1].parent.guild, debut="01/01/2020 21h")


def buttons_of(view) -> dict:
    return {b.label: b for b in view.walk_children() if isinstance(b, discord.ui.Button)}


async def test_cancelling_a_game(bot, game):
    record, thread = game
    interaction = click(bot)
    await buttons_of(await game_manage(bot, record))["Annuler la partie"].callback(interaction)
    buttons = buttons_of(interaction.response.sent[0]["view"])
    assert list(buttons) == ["Annuler la partie", "Garder la partie"]
    await buttons["Annuler la partie"].callback(interaction)
    assert "Partie annulée" in view_text(interaction.edited[0]["view"])

    panel = thread.messages[0].view
    assert "Partie annulée" in view_text(panel) and "Comment participer" not in view_text(panel)
    assert not [i for i in panel.walk_children() if isinstance(i, discord.ui.DynamicItem)]
    assert [t.name for t in thread.applied_tags] == ["Annulée"]
    assert bot.games.by_thread(thread.id) is None and await bot.games.repo.open() == []
    assert "plus en inscriptions" in view_text(await game_manage(bot, record))


@requires_ap_server
async def test_back_to_signup_after_a_wrong_start(bot, game, ap_server):
    record, thread = game
    await bot.games.upload(record, 1, [FakeAttachment("p1.yaml", (DEV_DIR / "players" / "p1.yaml").read_bytes())])
    track = bot.get_cog("track")
    start = {"lien": ap_server.address, "slot": "Carol", "mot_de_passe": None, "nom": None}
    await track.start.callback(track, FakeInteraction(guild=thread.parent.guild, channel_id=thread.id), **start)
    runtime = bot.rooms.by_thread(thread.id)

    interaction = click(bot)
    await buttons_of(await room_manage(bot, runtime))["Arrêter"].callback(interaction)
    buttons = buttons_of(interaction.response.sent[0]["view"])
    assert list(buttons) == ["Arrêter et publier le récap", "Arrêter sans récap", "Revenir aux inscriptions", "Annuler"]
    await buttons["Revenir aux inscriptions"].callback(interaction)
    assert "Retour aux inscriptions" in view_text(interaction.edited[0]["view"])

    assert bot.rooms.by_thread(thread.id) is None and await bot.rooms.repo.get(runtime.record.id) is None
    reopened = bot.games.by_thread(thread.id)
    assert reopened.id == record.id and reopened.status == "open"
    assert [t.name for t in thread.applied_tags] == ["Inscriptions"]
    panel = view_text(thread.messages[0].view)
    assert "Comment participer" in panel and "**Alice** · *Celeste 64* · <@1>" in panel
    assert "retour aux inscriptions" in thread.messages[-1].text

    await track.start.callback(track, FakeInteraction(guild=thread.parent.guild, channel_id=thread.id), **start)
    assert bot.rooms.by_thread(thread.id).claims == {1: 1}


def parts_of(view) -> dict:
    return {
        i.label if isinstance(i, discord.ui.Button) else i.placeholder: i
        for i in view.walk_children()
        if isinstance(i, discord.ui.Button | discord.ui.Select)
    }


async def test_an_apworld_for_another_game_is_named(bot, game):
    _, thread = game
    bot.games.core.games = frozenset({"Celeste"})
    cog = bot.get_cog("partie")
    files = [
        FakeAttachment("a.yaml", yaml("A", "Hacknet")),
        FakeAttachment("s.apworld", apworld("spire", "Slay the Spire")),
    ]
    message = FakeUserMessage(thread, attachments=files, author=FakeUser(1))
    await cog.on_message(message)
    text = view_text(message.replies[0]["view"])
    assert "*Hacknet* n'est pas un jeu officiel" in text and "Celui envoyé est pour *Slay the Spire*" in text


async def test_custom_games_need_their_apworld(bot, game):
    record, thread = game
    bot.games.core.games = frozenset({"Celeste"})
    cog = bot.get_cog("partie")
    message = FakeUserMessage(thread, attachments=[FakeAttachment("a.yaml", yaml("A", "Hacknet"))], author=FakeUser(1))
    await cog.on_message(message)
    report = message.replies[0]["view"]
    assert "*Hacknet* n'est pas un jeu officiel" in view_text(report) and "Mon yaml" in view_text(report)
    assert not parts_of(report)
    assert "⚠️ *Hacknet* · apworld manquant" in view_text(thread.messages[0].view)

    message = FakeUserMessage(thread, attachments=[FakeAttachment("Hacknet_1.apworld", apworld())], author=FakeUser(1))
    await cog.on_message(message)
    assert message.reactions == ["✅"]
    panel = view_text(thread.messages[0].view)
    assert "*Hacknet* v0.3.1 · <@1>" in panel and "manquant" not in panel

    interaction = click(bot, user=5)
    await YamlsZipButton(record.id).callback(interaction)
    [sent] = interaction.response.sent
    assert "**1 yaml** · **1 apworld**" in view_text(sent["view"]) and "custom_worlds" in view_text(sent["view"])
    assert sent["file"].filename == "archipelago.zip"
    archive = zipfile.ZipFile(io.BytesIO(sent["file"].fp.read()))
    assert archive.namelist() == ["Players/a.yaml", "custom_worlds/hacknet.apworld"]
    assert archive.read("custom_worlds/hacknet.apworld") == apworld()


async def test_apworlds_sent_before_are_offered_again(bot, game):
    record, thread = game
    bot.games.core.games = frozenset({"Celeste"})
    await bot.games.upload(record, 1, [FakeAttachment("hacknet.apworld", apworld(version="0.3.0"))])
    assert "*Hacknet* v0.3.0 · <@1>" in view_text(thread.messages[0].view)
    await bot.games.cancel(record)

    await new_game(bot, thread.parent.guild, nom="Dimanche")
    sunday, sunday_thread = bot.games.by_thread(thread.parent.threads[-1].id), thread.parent.threads[-1]
    interaction = click(bot, user=2)
    await YamlButton(sunday.id).callback(interaction)
    modal = interaction.response.sent[0]["modal"]
    modal.upload._values = [FakeAttachment("b.yaml", yaml("B", "Hacknet"))]
    submit = click(bot, user=2)
    await modal.on_submit(submit)
    report = submit.followup.sent[0]["view"]
    text = view_text(report)
    assert "Le serveur en a déjà un : utilise-le, ou envoie le tien." in text
    assert "*Hacknet* v0.3.0\n-# Envoyé sur le serveur par <@1> le " in text
    pick = click(bot, user=2)
    await parts_of(report)["Utiliser"].callback(pick)
    assert "Apworld choisi : *Hacknet* v0.3.0" in view_text(pick.response.sent[0]["view"])
    assert not parts_of(report)
    assert "*Hacknet* v0.3.0 · <@2>" in view_text(sunday_thread.messages[0].view)

    await bot.games.upload(sunday, 3, [FakeAttachment("hacknet.apworld", apworld())])
    interaction = click(bot, user=2)
    await YamlButton(sunday.id).callback(interaction)
    mine = interaction.response.sent[0]["view"]
    assert "pas un jeu officiel" not in view_text(mine) and not parts_of(mine).keys() - {
        "Retirer",
        "Envoyer un fichier",
        "Télécharger mes yamls",
    }

    await bot.games.remove_apworld(sunday, 3, (await bot.games.attached(sunday))[0].apworld.id)
    interaction = click(bot, user=2)
    await YamlButton(sunday.id).callback(interaction)
    mine = interaction.response.sent[0]["view"]
    select = parts_of(mine)["Choisir un apworld de Hacknet"]
    assert [o.label for o in select.options] == ["Hacknet v0.3.1", "Hacknet v0.3.0"]
    select._values = [select.options[1].value]
    await select.callback(click(bot, user=2))
    [attached] = await bot.games.attached(sunday)
    assert (attached.apworld.version, attached.user_id) == ("0.3.0", 2)
    assert "Choisir" not in parts_of(mine) and "Retirer" in parts_of(mine)

    await parts_of(mine)["Retirer"].callback(click(bot, user=2))
    assert await bot.games.attached(sunday) == [] and "Choisir un apworld de Hacknet" in parts_of(mine)
    assert "*Hacknet* · apworld manquant" in view_text(sunday_thread.messages[0].view)


async def test_apworlds_are_offered_when_core_games_are_unknown(bot, game):
    record, thread = game
    await bot.games.upload(record, 1, [FakeAttachment("a.yaml", yaml("A", "Hacknet"))])
    assert "Apworlds" not in view_text(thread.messages[0].view)
    report = await UploadReport.create(bot.games, record, 1, [])
    assert not report.needs_answer

    await bot.games.upload(record, 1, [FakeAttachment("hacknet.apworld", apworld()), FakeAttachment("x.apworld", b"")])
    await bot.games.remove_apworld(record, 1, (await bot.games.attached(record))[0].apworld.id)
    assert await bot.games.missing(record, await bot.games.files(record)) == {
        "Hacknet": await bot.games.apworlds.library(1, ["Hacknet"])
    }


async def test_big_bundles_are_split(bot, game):
    record, _ = game
    noise = random.Random(0).randbytes(200_000)
    await bot.games.upload(
        record,
        1,
        [
            FakeAttachment("a.yaml", yaml("A", "Hacknet")),
            FakeAttachment("hacknet.apworld", apworld(**{"hacknet/data.bin": noise})),
            FakeAttachment("tunic.apworld", apworld("tunic", "TUNIC", **{"tunic/data.bin": noise})),
        ],
    )
    bundle = await bot.games.bundle(record, ZIP_OVERHEAD + 250_000)
    assert (bundle.yamls, bundle.apworlds) == (1, 2)
    names = [zipfile.ZipFile(io.BytesIO(part)).namelist() for part in bundle.parts]
    assert names == [["Players/a.yaml", "custom_worlds/hacknet.apworld"], ["custom_worlds/tunic.apworld"]]


async def test_apworlds_sent_outside_a_game(bot, game):
    record, _ = game
    bot.games.core.games = frozenset({"Celeste"})
    cog = bot.get_cog("apworld")
    interaction = FakeInteraction(user=FakeUser(id=3))
    await cog.send.callback(cog, interaction, fichier=FakeAttachment("Hacknet_1.apworld", apworld()))
    assert "*Hacknet* v0.3.1 est sur le serveur" in view_text(interaction.followup.sent[0]["view"])
    assert [a.uploaded_by for a in await bot.games.apworlds.library(1, ["Hacknet"])] == [3]
    assert await bot.games.attached(record) == []

    with pytest.raises(UserError, match="Apworld refusé : ce n'est pas un apworld valide"):
        await cog.send.callback(cog, FakeInteraction(), fichier=FakeAttachment("x.apworld", b""))

    await bot.games.upload(record, 1, [FakeAttachment("a.yaml", yaml("A", "Hacknet"))])
    report = await UploadReport.create(bot.games, record, 1, [])
    assert "Utiliser" in parts_of(report) and "Envoyé sur le serveur par <@3>" in view_text(report)


async def test_unused_apworlds_are_not_regenerated(bot, game):
    record, thread = game
    bot.games.core.games = frozenset({"Celeste"})
    await bot.games.upload(record, 1, [FakeAttachment("a.yaml", yaml("A", "Hacknet"))])
    await bot.games.upload(record, 1, [FakeAttachment("hacknet.apworld", apworld())])
    await bot.games.upload(record, 2, [FakeAttachment("s.apworld", apworld("spire", "Slay the Spire"))])
    await bot.games.repo.set_status(record, "started")
    names = [name for name, _ in await bot.games.generation_files(thread.id, [])]
    assert names == ["Players/000_a.yaml", "custom_worlds/hacknet.apworld"]
    entries, _, _ = await bot.games.entries(record)
    assert "custom_worlds/spire.apworld" in [name for name, _ in entries]  # still handed to whoever generates

    # a game picked at random could be any of them
    await bot.games.upload(record, 3, [FakeAttachment("r.yaml", b"name: R\ngame:\n  Hacknet: 1\n  Celeste: 1\n")])
    names = [name for name, _ in await bot.games.generation_files(thread.id, [])]
    assert "custom_worlds/spire.apworld" in names


async def test_managers_replace_anyones_yaml_in_the_signups(bot, game):
    record, thread = game
    await bot.games.upload(record, 5, [FakeAttachment("opi.yaml", yaml("Opi"))])
    assert "Seule la personne" in view_text(await game_manage(bot, record, user=6))

    interaction = click(bot)
    await buttons_of(await game_manage(bot, record))["Yamls"].callback(interaction)
    view = interaction.response.sent[0]["view"]
    assert "`opi.yaml` · <@5>" in view_text(view) and "/track logique" not in view_text(view)
    send = click(bot)
    await parts_of(view)["Envoyer des yamls"].callback(send)
    modal = send.response.sent[0]["modal"]
    modal.upload._values = [FakeAttachment("fixed.yaml", yaml("Opi", "Hacknet"))]
    await modal.on_submit(click(bot))
    [fixed] = await bot.games.files(record)
    assert (fixed.filename, fixed.user_id, fixed.players[0].game) == ("fixed.yaml", 5, "Hacknet")
    assert "*Hacknet* · <@5>" in view_text(thread.messages[0].view)


@requires_ap_server
async def test_managers_fix_the_yamls_after_the_start(bot, game, ap_server):
    record, thread = game
    dev = {name: (DEV_DIR / "players" / name).read_bytes() for name in ("p1.yaml", "p2.yaml", "p3.yaml")}
    await bot.games.upload(record, 1, [FakeAttachment("p1.yaml", dev["p1.yaml"])])
    await bot.games.upload(record, 2, [FakeAttachment("p2.yaml", dev["p2.yaml"])])
    await bot.games.upload(record, 3, [FakeAttachment("old.yaml", yaml("Carole", "ChecksFinder"))])
    await bot.games.upload(record, 3, [FakeAttachment("extra.yaml", yaml("Dave", "Hacknet"))])
    track = bot.get_cog("track")
    start = {"lien": ap_server.address, "slot": "Carol", "mot_de_passe": None, "nom": None}
    await track.start.callback(track, FakeInteraction(guild=thread.parent.guild, channel_id=thread.id), **start)
    panel = thread.messages[0].view

    cog = bot.get_cog("partie")
    thread.parent.permissions = discord.Permissions.none()
    player = FakeUserMessage(thread, attachments=[FakeAttachment("x.yaml", yaml("X"))], author=FakeUser(id=5))
    await cog.on_message(player)
    assert not player.reactions and not player.replies
    runtime = bot.rooms.by_thread(thread.id)
    assert "Seule la personne" in view_text(await room_manage(bot, runtime, user=5))

    # the host, who started the tracking, adds the yaml that really generated Carol...
    fixed = FakeUserMessage(thread, attachments=[FakeAttachment("carol.yaml", dev["p3.yaml"])])
    await cog.on_message(fixed)
    assert fixed.reactions == ["✅"]
    # ...and replaces Alice's: it stays hers
    await bot.games.upload(record, 77, [FakeAttachment("alice.yaml", dev["p1.yaml"])], admin=True)
    owners = {f.filename: f.user_id for f in await bot.games.files(record)}
    assert owners == {"p2.yaml": 2, "old.yaml": 3, "extra.yaml": 3, "carol.yaml": 77, "alice.yaml": 1}

    interaction = click(bot)
    await buttons_of(await room_manage(bot, runtime))["Yamls"].callback(interaction)
    view = interaction.response.sent[0]["view"]
    assert "`carol.yaml` · <@77>\n**Carol** · *ChecksFinder*" in view_text(view)
    assert "/track logique" in view_text(view)
    files = {f.filename: f for f in await bot.games.files(record)}
    select = parts_of(view)["Retirer des yamls"]
    select._values = [str(files["old.yaml"].id), str(files["extra.yaml"].id)]
    pick = click(bot)
    await select.callback(pick)
    assert "extra.yaml" not in view_text(pick.response.sent[0]["view"])
    assert sorted(f.filename for f in await bot.games.files(record)) == ["alice.yaml", "carol.yaml", "p2.yaml"]
    assert thread.messages[0].view is panel  # the post still shows the room


@requires_ap_server
async def test_apworlds_chosen_after_the_start(bot, game, ap_server):
    record, thread = game
    bot.games.core.games = frozenset({"Celeste 64"})
    await bot.games.upload(record, 1, [FakeAttachment("p1.yaml", (DEV_DIR / "players" / "p1.yaml").read_bytes())])
    await bot.games.upload(record, 2, [FakeAttachment("b.yaml", yaml("B", "Hacknet"))])
    track = bot.get_cog("track")
    start = {"lien": ap_server.address, "slot": "Carol", "mot_de_passe": None, "nom": None}
    await track.start.callback(track, FakeInteraction(guild=thread.parent.guild, channel_id=thread.id), **start)

    runtime = bot.rooms.by_thread(thread.id)
    assert "Seule la personne" in view_text(await room_manage(bot, runtime, user=2))
    interaction = click(bot)
    await buttons_of(await room_manage(bot, runtime))["Apworlds"].callback(interaction)
    view = interaction.response.sent[0]["view"]
    assert "⚠️ *Hacknet* · apworld manquant · <@2>" in view_text(view) and "Aucun apworld de ce jeu" in view_text(view)
    assert "Celeste 64" not in view_text(view) and not parts_of(view)

    apworld_cog = bot.get_cog("apworld")
    for version in ("0.3.0", "0.3.1"):
        attachment = FakeAttachment("hacknet.apworld", apworld(version=version))
        await apworld_cog.send.callback(apworld_cog, FakeInteraction(user=FakeUser(id=9)), fichier=attachment)
    interaction = click(bot)
    await buttons_of(await room_manage(bot, runtime))["Apworlds"].callback(interaction)
    view = interaction.response.sent[0]["view"]
    select = parts_of(view)["Choisir l'apworld de Hacknet"]
    assert [o.label for o in select.options] == ["Hacknet v0.3.1", "Hacknet v0.3.0"]
    assert not any(o.default for o in select.options)
    select._values = [select.options[1].value]
    pick = click(bot)
    await select.callback(pick)
    [attached] = await bot.games.attached(record)
    assert (attached.apworld.version, attached.user_id) == ("0.3.0", 2)
    assert "*Hacknet* v0.3.0 · <@2>" in view_text(pick.response.sent[0]["view"])
    assert [o.default for o in parts_of(view)["Choisir l'apworld de Hacknet"].options] == [False, True]
    assert (await bot.games.generation_files(thread.id, []))[-1] == (
        "custom_worlds/hacknet.apworld",
        apworld(version="0.3.0"),
    )
