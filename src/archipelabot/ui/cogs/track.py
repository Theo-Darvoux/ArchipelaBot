import asyncio
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import discord
from discord import app_commands
from discord.ext import commands

from ...ap.client import APConnectionError, APRefused
from ...ap.webhost import WebhostError, parse_room_url
from ...core.progress import Progress
from ...core.room import RoomTracker
from ...errors import UserError
from ...storage.rooms import RoomRecord
from ..components import Tone, notice
from ..emojis import E
from ..forum import TAG_SPECS, RoomTag, ensure_room_tags
from ..render.panel import panel_view
from ..render.text import plural
from ..rooms import RoomRuntime
from ..types import Interaction
from ..views import ConfirmView

if TYPE_CHECKING:
    from ...bot import ArchipelaBot

REFUSAL_MESSAGES = {
    "InvalidSlot": "Ce slot n'existe pas dans la room.",
    "InvalidPassword": "Mot de passe incorrect.",
    "IncompatibleVersion": "La version du serveur n'est pas compatible avec le bot.",
}
WAKE_ATTEMPTS = 10
WAKE_DELAY = 2.0


@app_commands.guild_only()
class TrackCog(commands.GroupCog, name="track", group_name="track", group_description="Suivre une room Archipelago"):
    def __init__(self, bot: "ArchipelaBot") -> None:
        self.bot = bot

    # --- /track start -----------------------------------------------------------------------------

    @app_commands.command(name="start", description="Suivre une room dans le forum des rooms")
    @app_commands.describe(
        lien="Lien de la room (archipelago.gg/room/…)",
        slot="Nom d'un joueur, le bot l'utilisera pour se connecter en spectateur",
        mot_de_passe="Mot de passe du serveur, s'il y en a un",
        nom="Nom du post dans le forum",
    )
    async def start(
        self,
        interaction: Interaction,
        lien: app_commands.Range[str, 3, 200],
        slot: app_commands.Range[str, 1, 16] | None = None,
        mot_de_passe: str | None = None,
        nom: app_commands.Range[str, 1, 90] | None = None,
    ) -> None:
        guild = interaction.guild
        assert guild is not None
        config = await self.bot.guild_configs.get(guild.id)
        forum = guild.get_channel(config.forum_id) if config.forum_id else None
        if forum is None or forum.type != discord.ChannelType.forum:
            raise UserError("Aucun forum configuré pour les rooms. Un admin doit d'abord utiliser `/config forum`.")

        await interaction.response.defer(ephemeral=True, thinking=True)
        record = RoomRecord(
            guild_id=guild.id, name=nom or "", address=lien.strip(), slot=slot or "",
            password=mot_de_passe, created_by=interaction.user.id,
        )  # fmt: skip

        woke = False
        if webhost := parse_room_url(lien):
            record.webhost = webhost
            try:
                status = await self.bot.webhost.room_status(webhost)
                if not status.is_open():
                    await self.bot.webhost.wake(webhost)
                    woke = True
            except WebhostError as e:
                raise UserError(f"Impossible de lire cette room sur {webhost.host} ({e}).") from e
            record.slot = slot or (status.players[0][0] if status.players else "")
            if not record.slot:
                raise UserError("Cette room n'a aucun joueur.")
        elif not slot:
            raise UserError("Indique aussi `slot` : le nom d'un des joueurs. ")

        if duplicate := self.bot.rooms.find_duplicate(guild.id, record):
            raise UserError(f"Cette room est déjà suivie dans <#{duplicate.record.thread_id}>.")

        tracker = await self._connect(record, woke)
        state = tracker.state
        record.name = record.name or f"Multiworld · {len(state.players)} joueurs · {datetime.now(UTC):%d/%m}"

        try:
            tags = await ensure_room_tags(forum)
            created = await forum.create_thread(
                name=record.name,
                view=panel_view(record.name, state, Progress(state), since=record.created_at,
                                page_url=webhost.page_url if webhost else None),
                applied_tags=[tags[RoomTag.ACTIVE]],
                auto_archive_duration=10080,
                reason=f"Room suivie par {interaction.user}",
            )  # fmt: skip
        except discord.HTTPException as e:
            await tracker.stop()
            raise UserError(f"Impossible de créer le post dans {forum.mention} ({e.text or e.status}).") from e

        record.thread_id = created.thread.id
        record.panel_message_id = created.message.id
        await self.bot.rooms.repo.create(record)
        runtime = await self.bot.rooms.attach(record, tracker)
        recognised = await self.bot.rooms.auto_claim(runtime)
        runtime.panel.request_update(urgent=True)

        games = {s.game for s in state.players}
        text = f"Room suivie · {len(state.players)} joueurs · {len(games)} jeux\n→ {created.thread.mention}"
        if recognised:
            text += f"\n-# {plural(recognised, 'joueur reconnu', 'joueurs reconnus')} d'après les parties précédentes."
        await interaction.followup.send(view=notice(text, Tone.SUCCESS), ephemeral=True)

    async def _connect(self, record: RoomRecord, woke: bool) -> RoomTracker:
        tracker = self.bot.rooms.build_tracker(record)
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
                await tracker.start()
                return tracker
            except APRefused as e:
                reasons = [REFUSAL_MESSAGES.get(err, err) for err in e.errors]
                raise UserError("Le serveur a refusé la connexion : " + " ".join(reasons)) from e
            except APConnectionError as e:
                if attempt == attempts - 1:
                    raise UserError(f"Impossible de joindre le serveur `{record.address}`.") from e
                await asyncio.sleep(WAKE_DELAY)
        raise UserError("La room ne s'est pas réveillée à temps. Réessaie dans un instant.")

    # --- commands inside a room's post --------------------------------------------------------

    def _room_here(self, interaction: Interaction) -> RoomRuntime:
        runtime = self.bot.rooms.by_thread(interaction.channel_id)
        if runtime is None:
            raise UserError("Utilise cette commande dans le post d'une room suivie.")
        return runtime

    def _check_manager(self, interaction: Interaction, runtime: RoomRuntime) -> None:
        if not runtime.can_manage(interaction.user.id, interaction.permissions):
            raise UserError("Seule la personne qui a lancé le suivi (ou un modérateur) peut faire ça.")

    @app_commands.command(name="stop", description="Arrêter le suivi de cette room")
    async def stop(self, interaction: Interaction) -> None:
        runtime = self._room_here(interaction)
        self._check_manager(interaction, runtime)

        async def stop_with_recap() -> str:
            await self.bot.rooms.publish_recap(runtime)
            await self.bot.rooms.stop(runtime, "finished")
            return f"{E.stopped} Suivi arrêté, récap publié."

        async def stop() -> str:
            await self.bot.rooms.stop(runtime, "stopped")
            return f"{E.stopped} Suivi arrêté. Le post reste consultable."

        view = ConfirmView(
            f"Arrêter le suivi de **{runtime.record.name}** ?\n-# Le post restera consultable, avec le tag "
            f"{' '.join(TAG_SPECS[RoomTag.FINISHED][::-1])}.",
            [
                ("Arrêter et publier le récap", discord.ButtonStyle.primary, stop_with_recap),
                ("Arrêter sans récap", discord.ButtonStyle.danger, stop),
            ],
        )
        await interaction.response.send_message(view=view, ephemeral=True)

    @app_commands.command(name="reglages", description="Choisir ce qui est affiché dans le fil de cette room")
    async def settings(self, interaction: Interaction) -> None:
        runtime = self._room_here(interaction)
        self._check_manager(interaction, runtime)
        await interaction.response.send_message(view=runtime.settings_view(), ephemeral=True)


async def setup(bot: "ArchipelaBot") -> None:
    await bot.add_cog(TrackCog(bot))
