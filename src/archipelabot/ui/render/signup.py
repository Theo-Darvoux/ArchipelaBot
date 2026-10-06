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
    return fit_lines(lines, ROSTER_BUDGET, lambda hidden: f"… et {hidden} autres") or "*Aucun yaml pour l'instant.*"


def signup_view(
    game: GameRecord,
    files: Sequence[YamlFile],
    *,
    ping_role_id: int | None = None,
    buttons: list[ui.Item] | None = None,
) -> ui.LayoutView:
    slots = sum(len(f.players) for f in files)
    people = len({f.user_id for f in files})
    header = f"## {md(game.name)}\n{E.signup} **Inscriptions ouvertes** · {plural(slots, 'slot')} · " + plural(
        people, "joueur"
    )
    if game.description:
        header += f"\n{game.description}"
    if ping_role_id:
        header += f"\n{E.ping} <@&{ping_role_id}>"
    header += (
        f"\n-# Créée par <@{game.created_by}> {discord.utils.format_dt(game.created_at, 'R')}"
        "\n-# Envoie ton yaml avec le bouton ou dépose-le dans ce post. Quand la room est générée, colle son lien "
        "ici (`archipelago.gg/room/…`) : le suivi démarre tout seul."
    )
    view = ui.LayoutView(timeout=None)
    view.add_item(
        ui.Container(
            ui.TextDisplay(header),
            ui.Separator(),
            ui.TextDisplay(signup_roster(files)),
            *([ui.Separator(), ui.ActionRow(*buttons)] if buttons else []),
            accent_colour=Tone.INFO.value,
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
