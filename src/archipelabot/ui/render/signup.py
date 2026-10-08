from collections.abc import Sequence
from dataclasses import dataclass, field

import discord
from discord import ui

from ...core.yamls import PlayerYaml, YamlFile
from ...storage.games import GameRecord
from ..components import Tone
from ..emojis import E
from .text import fit_lines, md, plural

ROSTER_BUDGET = 3000


def player_label(player: PlayerYaml) -> str:
    if player.final_name is not None:
        return f"**{md(player.final_name)}**"
    return f"**{md(player.name)}** *(nom provisoire)*"


def game_label(player: PlayerYaml) -> str:
    return f"*{md(player.game)}*" if player.game else f"{E.random} *jeu aléatoire*"


def signup_roster(files: Sequence[YamlFile]) -> str:
    lines = [
        f"{E.yaml} {player_label(player)} · {game_label(player)} · <@{f.user_id}>"
        for f in files
        for player in f.players
    ]
    return fit_lines(lines, ROSTER_BUDGET, lambda hidden: f"… et {hidden} autres") or "*Personne pour l'instant.*"


def signup_view(
    game: GameRecord,
    files: Sequence[YamlFile],
    *,
    track_command: str = "`/track start`",
    ping_role_id: int | None = None,
    buttons: list[ui.Item] | None = None,
) -> ui.LayoutView:
    header = f"## {md(game.name)}"
    if game.starts_at:
        start = game.starts_at
        header += f"\n{E.calendar} **{discord.utils.format_dt(start, 'F')}** · {discord.utils.format_dt(start, 'R')}"
    if game.description:
        header += "\n" + "\n".join(f"> {line}" for line in game.description.splitlines())
    header += f"\n-# Organisée par <@{game.created_by}>" + (f" · pour <@&{ping_role_id}>" if ping_role_id else "")
    cancelled = game.status == "cancelled"
    if cancelled:
        header += f"\n### {E.stopped} Partie annulée"

    slots = sum(len(f.players) for f in files)
    people = len({f.user_id for f in files})
    count = plural(people, "joueur") + (f" · {plural(slots, 'slot')}" if slots != people else "")
    roster = f"### {E.signup} Inscrits" + (f" · {count}" if files else "") + f"\n{signup_roster(files)}"

    steps = (
        "### Comment participer\n"
        f"**1.** Clique sur le bouton **{E.yaml} Mon yaml** pour **envoyer**, **modifier**, ou **retirer** ton "
        "fichier `.yaml` tant que la partie n'a pas commencé. Tu peux aussi le glisser dans ce post.\n"
        f"**2.** Quand tout le monde est inscrit, l'organisateur récupère **{E.zip} Tous les yamls** et génère "
        "la partie.\n"
        f"**3.** Il lance ensuite {track_command} ici avec le lien de la room : ce message devient le suivi de la "
        "partie et chacun est relié à son slot."
    )
    view = ui.LayoutView(timeout=None)
    view.add_item(
        ui.Container(
            ui.TextDisplay(header),
            ui.Separator(),
            ui.TextDisplay(roster),
            *([] if cancelled else [ui.Separator(), ui.TextDisplay(steps)]),
            *([ui.Separator(), ui.ActionRow(*buttons)] if buttons and not cancelled else []),
            accent_colour=discord.Colour.dark_grey() if cancelled else Tone.INFO.value,
        )
    )
    return view


@dataclass(slots=True)
class Upload:
    filename: str
    players: list[PlayerYaml] = field(default_factory=list)
    error: str | None = None
    warnings: list[str] = field(default_factory=list)
    replaced: list[str] = field(default_factory=list)

    @property
    def clean(self) -> bool:
        return self.error is None and not self.warnings


def upload_text(upload: Upload) -> str:
    if upload.error:
        return f"{E.error} **{md(upload.filename)}** : {upload.error}"
    slots = ", ".join(f"{player_label(p)} · {game_label(p)}" for p in upload.players)
    text = f"{E.yaml} **{md(upload.filename)}** : {slots}"
    if upload.replaced:
        text += "\n-# Remplace " + ", ".join(f"`{md(name)}`" for name in upload.replaced)
    return text + "".join(f"\n-# ⚠️ {warning}" for warning in upload.warnings)


def upload_report(uploads: Sequence[Upload]) -> ui.LayoutView:
    failed = sum(u.error is not None for u in uploads)
    tone = Tone.SUCCESS if not failed else Tone.ERROR if failed == len(uploads) else Tone.WARNING
    view = ui.LayoutView()
    view.add_item(ui.Container(ui.TextDisplay("\n".join(upload_text(u) for u in uploads)), accent_colour=tone.value))
    return view
