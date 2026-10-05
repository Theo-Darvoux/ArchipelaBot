"""Wires each tracked room to its forum post: feed, panel and tags."""

import asyncio
import contextlib
import io
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

import discord
from discord import ui

from ..ap.client import ConnectOptions
from ..core import events as ev
from ..core.chat import ChatRelay
from ..core.progress import Progress, direct_baseline, webhost_baseline
from ..core.room import AddressResolver, RoomTracker
from ..errors import UserError
from ..recap.chart import render_chart
from ..recap.stats import Recap, build_recap
from ..storage.claims import ClaimRepo, NotifMode, NotifPrefs
from ..storage.history import HistoryRepo
from ..storage.rooms import RoomRecord, RoomRepo, RoomStatus
from .forum import TAG_SPECS, RoomTag
from .panel_buttons import ClaimButton, MyHintsButton, SettingsButton
from .render.feed import feed_view
from .render.panel import panel_view
from .render.recap import CHART_FILENAME, recap_view
from .render.text import md
from .services.feed import FeedMessage, FeedService
from .services.notify import NotifyService
from .services.panel import PanelService
from .services.presence import Presence
from .services.progress import BaselineFetcher, ProgressService
from .views import SettingsView

if TYPE_CHECKING:
    from ..bot import ArchipelaBot

log = logging.getLogger(__name__)

BOT_TAGS = ("TextOnly", "DeathLink")


class ThreadSink:
    """Everything the services post goes through here."""

    def __init__(self, bot: "ArchipelaBot", record: RoomRecord) -> None:
        self.bot = bot
        self.record = record

    async def thread(self) -> discord.Thread:
        thread_id = self.record.thread_id
        channel = self.bot.get_channel(thread_id) or await self.bot.fetch_channel(thread_id)
        if channel.type != discord.ChannelType.public_thread:
            raise TypeError(f"channel {thread_id} is not a forum post")
        return channel  # type: ignore[return-value]

    @property
    def jump_url(self) -> str:
        return f"https://discord.com/channels/{self.record.guild_id}/{self.record.thread_id}"

    async def send_lines(self, message: FeedMessage) -> None:
        allowed = discord.AllowedMentions(users=[discord.Object(user) for user in message.mentions])
        await (await self.thread()).send(view=feed_view(list(message.lines)), allowed_mentions=allowed)

    async def send_dm(self, user_id: int, view: ui.LayoutView) -> None:
        user = self.bot.get_user(user_id) or await self.bot.fetch_user(user_id)
        await user.send(view=view)

    async def send_view(self, view: ui.LayoutView) -> None:
        await (await self.thread()).send(view=view)

    async def edit_panel(self, view: ui.LayoutView) -> None:
        thread = await self.thread()
        await thread.get_partial_message(self.record.panel_message_id).edit(view=view)

    async def delete_message(self, message_id: int) -> None:
        await (await self.thread()).get_partial_message(message_id).delete()

    async def set_tag(self, tag: RoomTag) -> None:
        thread = await self.thread()
        if thread.parent is None or thread.parent.type != discord.ChannelType.forum:
            return
        ours = {name for name, _ in TAG_SPECS.values()}
        wanted = TAG_SPECS[tag][0]
        tags = [t for t in thread.applied_tags if t.name not in ours]
        tags += [t for t in thread.parent.available_tags if t.name == wanted]
        if {t.id for t in tags} != {t.id for t in thread.applied_tags}:
            await thread.edit(applied_tags=tags[:5])


@dataclass(eq=False)
class RoomRuntime:
    record: RoomRecord
    tracker: RoomTracker
    sink: ThreadSink
    repo: RoomRepo
    claim_repo: ClaimRepo
    prefs: NotifPrefs
    history: HistoryRepo
    fetch_baseline: BaselineFetcher
    claims: dict[int, int] = field(default_factory=dict)  # slot -> Discord user
    tasks: list[asyncio.Task] = field(default_factory=list)
    on_everyone_finished: Callable[[], None] = lambda: None

    def __post_init__(self) -> None:
        self.progress = Progress(self.tracker.state)
        self.progress_service = ProgressService(
            self.record.id,
            self.tracker.state,
            self.progress,
            self.history,
            self.fetch_baseline,
            lambda urgent: self.panel.request_update(urgent=urgent),
            periodic=self.record.webhost is not None,
        )
        self.presence = Presence(self.tracker.state)
        self.feed = FeedService(
            self.tracker.state, lambda: self.record.settings, self.sink, ping=self.ping_target, presence=self.presence
        )
        self.notify = NotifyService(
            self.tracker.state,
            lambda: self.record.name,
            lambda: self.sink.jump_url,
            self.dm_target,
            self.sink,
            presence=self.presence,
        )
        self.panel = PanelService(self.render_panel, self.sink.edit_panel)
        self.chat = ChatRelay(self.tracker, self.record.password)

    def _claimant_with_mode(self, slot: int, mode: NotifMode) -> int | None:
        user = self.claims.get(slot)
        return user if user is not None and self.prefs.mode(user) == mode else None

    def ping_target(self, slot: int) -> int | None:
        return self._claimant_with_mode(slot, NotifMode.THREAD)

    def dm_target(self, slot: int) -> int | None:
        return self._claimant_with_mode(slot, NotifMode.DM)

    @property
    def active(self) -> bool:
        return self.tracker.state.connection != ev.ConnectionState.STOPPED

    def render_panel(self, *, buttons: bool = True) -> ui.LayoutView:
        webhost = self.record.webhost
        return panel_view(
            self.record.name,
            self.tracker.state,
            self.progress,
            since=self.record.created_at,
            claims=self.claims,
            page_url=webhost.page_url if webhost else None,
            buttons=self._buttons(ClaimButton, MyHintsButton, SettingsButton) if buttons else None,
        )

    def _buttons(self, *kinds: type[ui.DynamicItem]) -> list[ui.Item] | None:
        return [kind(self.record.id) for kind in kinds] if self.active else None

    async def remove_hint_board(self) -> None:
        """Hints used to have a public board in the post; they're now shown on demand only."""
        if self.record.hints_message_id is not None:
            try:
                await self.sink.delete_message(self.record.hints_message_id)
            except discord.NotFound:
                pass
            self.record.hints_message_id = None
            await self.repo.save_hints_message(self.record)

    def can_manage(self, user_id: int, permissions: discord.Permissions) -> bool:
        return user_id == self.record.created_by or permissions.manage_threads

    def settings_view(self) -> SettingsView:
        async def save(settings) -> None:
            self.record.settings = settings
            await self.repo.save_settings(self.record)

        return SettingsView(self.record.name, self.record.settings.model_copy(), save)

    def slot_named(self, name: str) -> int:
        for info in self.tracker.state.players:
            if info.name.casefold() == name.strip().casefold():
                return info.slot
        raise UserError(f"Aucun joueur nommé **{md(name)}** dans cette room.")

    async def claim(self, slot: int, user_id: int, *, force: bool = False) -> None:
        owner = self.claims.get(slot)
        if owner is not None and owner != user_id and not force:
            raise UserError(f"Ce slot est déjà pris par <@{owner}>.")
        self.claims[slot] = user_id
        await self.claim_repo.set(self.record.id, slot, self.tracker.state.name(slot), user_id)
        self.panel.request_update(urgent=True)

    async def unclaim(self, slot: int) -> None:
        self.claims.pop(slot, None)
        await self.claim_repo.remove(self.record.id, slot)
        self.panel.request_update(urgent=True)

    @property
    def everyone_finished(self) -> bool:
        players = self.tracker.state.players
        return bool(players) and all(self.progress.reached_goal(s.slot) for s in players)

    async def build_recap(self) -> Recap:
        return build_recap(
            self.record.name,
            self.record.created_at,
            datetime.now(UTC),
            self.tracker.state,
            self.progress,
            await self.history.events(self.record.id),
            await self.history.snapshots(self.record.id),
        )

    async def on_event(self, event: ev.Event) -> None:
        await self.progress_service.handle(event)
        await self.presence.handle(event)
        await self.feed.handle(event)
        await self.notify.handle(event)
        if isinstance(event, ev.GoalReached | ev.ClientStatusChanged) and self.everyone_finished:
            self.on_everyone_finished()
        if isinstance(event, ev.ConnectionChanged):
            self.panel.request_update(urgent=True)
            await self._on_connection(event)

    async def _on_connection(self, event: ev.ConnectionChanged) -> None:
        if event.state == ev.ConnectionState.CONNECTED and event.address != self.record.address:
            self.record.address = event.address
            await self.repo.save_address(self.record)
        tag = {ev.ConnectionState.CONNECTED: RoomTag.ACTIVE, ev.ConnectionState.ASLEEP: RoomTag.ASLEEP}.get(event.state)
        if tag:
            try:
                await self.sink.set_tag(tag)
            except discord.HTTPException:
                log.warning("Could not update the tags of room %s", self.record.id, exc_info=True)


class RoomManager:
    def __init__(self, bot: "ArchipelaBot") -> None:
        self.bot = bot
        self.repo = RoomRepo(bot.db)
        self.claim_repo = ClaimRepo(bot.db)
        self.history = HistoryRepo(bot.db)
        self._finishing: dict[int, asyncio.Task[None]] = {}
        self._rooms: dict[int, RoomRuntime] = {}

    def get(self, room_id: int) -> RoomRuntime | None:
        return self._rooms.get(room_id)

    def by_thread(self, thread_id: int | None) -> RoomRuntime | None:
        return next((r for r in self._rooms.values() if r.record.thread_id == thread_id), None)

    def find_duplicate(self, guild_id: int, record: RoomRecord) -> RoomRuntime | None:
        for runtime in self._rooms.values():
            other = runtime.record
            if other.guild_id != guild_id:
                continue
            if (record.webhost and record.webhost == other.webhost) or (
                not record.webhost and record.address == other.address
            ):
                return runtime
        return None

    def build_tracker(self, record: RoomRecord) -> RoomTracker:
        resolve: AddressResolver | None = None
        if webhost := record.webhost:

            async def resolve_webhost() -> str | None:
                return await self.bot.webhost.current_address(webhost)

            resolve = resolve_webhost

        options = ConnectOptions(slot=record.slot, password=record.password, tags=BOT_TAGS)
        return RoomTracker(record.address, options, self.bot.datapackages, resolve_address=resolve)

    async def attach(self, record: RoomRecord, tracker: RoomTracker) -> RoomRuntime:
        """Start posting a tracker's events in the record's forum post."""
        assert record.id is not None
        runtime = RoomRuntime(
            record=record,
            on_everyone_finished=lambda: self._finish_soon(record.id),
            tracker=tracker,
            sink=ThreadSink(self.bot, record),
            repo=self.repo,
            claim_repo=self.claim_repo,
            prefs=self.bot.notif_prefs,
            history=self.history,
            fetch_baseline=self._baseline_fetcher(record, tracker),
            claims=await self.claim_repo.for_room(record.id),
        )
        await runtime.progress_service.load()
        tracker.subscribe(runtime.on_event)
        runtime.tasks = [
            asyncio.create_task(runtime.progress_service.run(), name=f"progress {record.id}"),
            asyncio.create_task(runtime.feed.run(), name=f"feed {record.id}"),
            asyncio.create_task(runtime.notify.run(), name=f"notify {record.id}"),
            asyncio.create_task(runtime.panel.run(), name=f"panel {record.id}"),
            asyncio.create_task(runtime.chat.run(), name=f"chat {record.id}"),
        ]
        try:
            await runtime.remove_hint_board()
        except discord.HTTPException:
            log.warning("Could not delete the hint board of room %s", record.id, exc_info=True)
        self._rooms[record.id] = runtime
        return runtime

    def _baseline_fetcher(self, record: RoomRecord, tracker: RoomTracker) -> BaselineFetcher:
        state = tracker.state
        if webhost := record.webhost:
            return lambda: webhost_baseline(self.bot.webhost, webhost, state.team)
        return lambda: direct_baseline(state.address, record.password, state)

    async def auto_claim(self, runtime: RoomRuntime) -> int:
        """Give slots to whoever played them in this guild's earlier rooms. Returns how many were claimed."""
        names = {info.name: info.slot for info in runtime.tracker.state.players if info.slot not in runtime.claims}
        previous = await self.claim_repo.previous(runtime.record.guild_id, list(names))
        for name, user in previous.items():
            await runtime.claim(names[name], user)
        return len(previous)

    def _finish_soon(self, room_id: int) -> None:
        # Not awaited: stopping the room cancels the tracker task that is delivering this event.
        if room_id not in self._finishing:
            self._finishing[room_id] = asyncio.create_task(self._finish(room_id), name=f"finish {room_id}")

    async def _finish(self, room_id: int) -> None:
        runtime = self._rooms.get(room_id)
        if runtime is None:
            return
        await runtime.feed.flush()
        try:
            await self.publish_recap(runtime)
        except Exception:
            log.exception("Could not publish the recap of room %s", room_id)
        await self.stop(runtime, "finished")

    async def publish_recap(self, runtime: RoomRuntime) -> None:
        """Post the recap in the room's post, and in the guild's recap channel if there is one."""
        recap = await runtime.build_recap()
        tz = ZoneInfo(self.bot.settings.timezone)
        state = runtime.tracker.state
        chart = await asyncio.to_thread(render_chart, recap, state.name, tz) if recap.series else None

        async def send(channel: discord.abc.Messageable, thread_url: str | None) -> None:
            view = recap_view(recap, state, chart=chart is not None, thread_url=thread_url)
            files = [discord.File(io.BytesIO(chart), CHART_FILENAME)] if chart else []
            await channel.send(view=view, files=files)

        await send(await runtime.sink.thread(), None)
        config = await self.bot.guild_configs.get(runtime.record.guild_id)
        if config.recap_channel_id:
            channel = self.bot.get_channel(config.recap_channel_id) or await self.bot.fetch_channel(
                config.recap_channel_id
            )
            await send(channel, runtime.sink.jump_url)

    async def restore(self) -> None:
        """Resume every active room after a restart; they reconnect in the background."""
        for record in await self.repo.active():
            tracker = self.build_tracker(record)
            runtime = await self.attach(record, tracker)
            # The layouts may have changed with a new version of the bot.
            runtime.panel.request_update()
            tracker.resume()
        log.info("Resumed %d rooms", len(self._rooms))

    async def stop(self, runtime: RoomRuntime, status: RoomStatus) -> None:
        await self._detach(runtime)
        await self.repo.set_status(runtime.record, status)
        try:
            await runtime.sink.edit_panel(runtime.render_panel())
            await runtime.sink.set_tag(RoomTag.FINISHED)
        except discord.HTTPException:
            log.warning("Could not finalize the post of room %s", runtime.record.id, exc_info=True)

    async def shutdown(self) -> None:
        for runtime in list(self._rooms.values()):
            await self._detach(runtime)

    async def _detach(self, runtime: RoomRuntime) -> None:
        self._rooms.pop(runtime.record.id, None)
        await runtime.chat.close_all()
        await runtime.tracker.stop()
        for task in runtime.tasks:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        with contextlib.suppress(Exception):
            await runtime.feed.flush()
            await runtime.notify.flush()
            await runtime.progress_service.snapshot()
