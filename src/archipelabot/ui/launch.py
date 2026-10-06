import asyncio
import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import discord

from ..ap.client import APConnectionError, APRefused
from ..ap.webhost import WebhostError, is_web_link, parse_room_url
from ..core.progress import Progress
from ..core.room import RoomTracker
from ..errors import UserError
from ..storage.games import GameRecord
from ..storage.rooms import RoomRecord
from .components import Tone, notice
from .emojis import E
from .forum import RoomTag, ensure_room_tags
from .render.panel import panel_view
from .rooms import RoomRuntime, ThreadSink

if TYPE_CHECKING:
    from ..bot import ArchipelaBot

log = logging.getLogger(__name__)

REFUSAL_MESSAGES = {
    "InvalidSlot": "Ce slot n'existe pas dans la room.",
    "InvalidPassword": "Mot de passe incorrect.",
    "IncompatibleVersion": "La version du serveur n'est pas compatible avec le bot.",
}
WAKE_ATTEMPTS = 10
WAKE_DELAY = 2.0


async def launch_room(
    bot: "ArchipelaBot",
    guild: discord.Guild,
    link: str,
    *,
    user_id: int,
    slot: str | None = None,
    password: str | None = None,
    name: str | None = None,
    game: GameRecord | None = None,
) -> tuple[RoomRuntime, int]:
    """Track a room in a new forum post, or in `game`'s post. Returns the room and how many players were recognised."""
    if game is None:
        return await _launch(bot, guild, link, user_id, slot, password, name, None)
    async with bot.games.starting(game):
        return await _launch(bot, guild, link, game.created_by, slot, password, name or game.name, game)


async def _launch(
    bot: "ArchipelaBot",
    guild: discord.Guild,
    link: str,
    user_id: int,
    slot: str | None,
    password: str | None,
    name: str | None,
    game: GameRecord | None,
) -> tuple[RoomRuntime, int]:
    webhost = parse_room_url(link)
    if webhost is None and is_web_link(link):
        kind = "d'un tracker" if "/tracker/" in link else "d'une page qui n'est pas une room"
        raise UserError(
            f"C'est le lien {kind}. Colle le lien de la room (`archipelago.gg/room/…`) ou l'adresse du serveur "
            "(`hôte:port`)."
        )
    forum = None
    if game is None:
        config = await bot.guild_configs.get(guild.id)
        forum = guild.get_channel(config.forum_id) if config.forum_id else None
        if forum is None or forum.type != discord.ChannelType.forum:
            raise UserError("Aucun forum configuré pour les rooms. Un admin doit d'abord utiliser `/config forum`.")

    record = RoomRecord(
        guild_id=guild.id, name=name or "", address=link.strip(), slot=slot or "", password=password,
        created_by=user_id,
    )  # fmt: skip
    woke = False
    if webhost:
        record.webhost = webhost
        try:
            status = await bot.webhost.room_status(webhost)
            if not status.is_open():
                await bot.webhost.wake(webhost)
                woke = True
        except WebhostError as e:
            raise UserError(f"Impossible de lire cette room sur {webhost.host} ({e}).") from e
        record.slot = slot or (status.players[0][0] if status.players else "")
        if not record.slot:
            raise UserError("Cette room n'a aucun joueur.")
    elif not slot:
        raise UserError("Indique aussi `slot` : le nom d'un des joueurs. ")

    if duplicate := bot.rooms.find_duplicate(guild.id, record):
        raise UserError(f"Cette room est déjà suivie dans <#{duplicate.record.thread_id}>.")

    tracker = await _connect(bot, record, woke)
    state = tracker.state
    record.name = record.name or f"Multiworld · {len(state.players)} joueurs · {datetime.now(UTC):%d/%m}"
    view = panel_view(
        record.name, state, Progress(state), since=record.created_at, page_url=webhost.page_url if webhost else None
    )
    try:
        if game is not None:
            record.thread_id, record.panel_message_id = game.thread_id, game.panel_message_id
            sink = ThreadSink(bot, record)
            await sink.edit_panel(view)
            await sink.set_tag(RoomTag.ACTIVE)
        else:
            tags = await ensure_room_tags(forum)  # type: ignore[arg-type]
            created = await forum.create_thread(  # type: ignore[union-attr]
                name=record.name,
                view=view,
                applied_tags=[tags[RoomTag.ACTIVE]],
                auto_archive_duration=10080,
                reason="Room suivie",
            )
            record.thread_id, record.panel_message_id = created.thread.id, created.message.id
    except discord.HTTPException as e:
        await tracker.stop()
        raise UserError(f"Impossible de préparer le post de la room ({e.text or e.status}).") from e

    await bot.rooms.repo.create(record)
    runtime = await bot.rooms.attach(record, tracker)
    tracker.listen()
    participants = await bot.games.started(game, runtime) if game is not None else []
    recognised = await bot.rooms.auto_claim(runtime)
    runtime.panel.request_update(urgent=True)
    if game is not None:
        await _announce(bot, runtime, participants)
    return runtime, recognised


async def _connect(bot: "ArchipelaBot", record: RoomRecord, woke: bool) -> RoomTracker:
    tracker = bot.rooms.build_tracker(record)
    attempts = WAKE_ATTEMPTS if woke else 1
    for attempt in range(attempts):
        if tracker.resolve_address:
            try:
                address = await tracker.resolve_address()
            except WebhostError as e:
                raise UserError(f"Impossible de lire cette room ({e}).") from e
            if address is None:
                await asyncio.sleep(WAKE_DELAY)
                continue
            tracker.state.address = record.address = address
        try:
            await tracker.start(listen=False)
            return tracker
        except APRefused as e:
            reasons = [REFUSAL_MESSAGES.get(err, err) for err in e.errors]
            raise UserError("Le serveur a refusé la connexion : " + " ".join(reasons)) from e
        except (APConnectionError, TimeoutError) as e:
            if attempt == attempts - 1:
                raise UserError(f"Impossible de joindre le serveur `{record.address}`.") from e
            await asyncio.sleep(WAKE_DELAY)
    raise UserError("La room ne s'est pas réveillée à temps. Réessaie dans un instant.")


async def _announce(bot: "ArchipelaBot", runtime: RoomRuntime, participants: list[int]) -> None:
    config = await bot.guild_configs.get(runtime.record.guild_id)
    roles = [config.ping_role_id] if config.ping_role_id else []
    mentions = " ".join([*(f"<@&{role}>" for role in roles), *(f"<@{user}>" for user in participants)])
    webhost = runtime.record.webhost
    link = f" · [ouvrir la room]({webhost.page_url})" if webhost else f" · `{runtime.tracker.state.address}`"
    text = f"### {E.online} La room est ouverte !{link}\n{mentions}".rstrip()
    allowed = discord.AllowedMentions(
        roles=[discord.Object(r) for r in roles], users=[discord.Object(u) for u in participants]
    )
    try:
        await runtime.sink.send_view(notice(text, Tone.SUCCESS), allowed_mentions=allowed)
    except discord.HTTPException:
        log.warning("Could not announce the opening of room %s", runtime.record.id, exc_info=True)
