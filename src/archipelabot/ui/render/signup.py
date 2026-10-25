from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

import discord
from discord import ui

from ...core.apworlds import Apworld, Attached
from ...core.yamls import PlayerYaml, YamlFile
from ...storage.games import GameRecord
from ..components import Tone
from ..emojis import E
from .text import code, fit_lines, md, plural

# A message's text displays share 4000 characters: the roster gets what the other sections leave.
TEXT_LIMIT = 4000
APWORLDS_BUDGET = 1200
INVALID_KEPT = "Il est gardé quand même, mais la génération échouera tant qu'il n'est pas corrigé."


def player_label(player: PlayerYaml) -> str:
    if player.final_name is not None:
        return f"**{md(player.final_name)}**"
    return f"**{md(player.name)}** *(nom provisoire)*"


def game_label(player: PlayerYaml) -> str:
    return f"*{md(player.game)}*" if player.game else f"{E.random} *jeu aléatoire*"


def apworld_label(apworld: Apworld) -> str:
    version = f" v{md(apworld.version)}" if apworld.version else ""
    return f"*{md(apworld.game)}*{version}"


def missing_text(game: str, library: Sequence[Apworld], stray: Sequence[Apworld] = ()) -> str:
    text = f"{E.apworld} *{md(game)}* n'est pas un jeu officiel : il faut son `.apworld`."
    if stray:
        games = ", ".join(sorted({f"*{md(a.game)}*" for a in stray}))
        sent = "Celui envoyé est pour" if len(stray) == 1 else "Ceux envoyés sont pour"
        text += f" {sent} {games}, pas pour ce jeu."
    if len(library) == 1:
        return text + " Le serveur en a déjà un : utilise-le, ou envoie le tien."
    if library:
        return text + f" Le serveur en a déjà {len(library)} : choisis-en un, ou envoie le tien."
    return text + f" Envoie-le avec {E.yaml} **Mon yaml** ou dépose-le dans le post."


def library_text(apworld: Apworld) -> str:
    sent = f" le {apworld.uploaded_at:%d/%m/%Y}" if apworld.uploaded_at else ""
    return f"{E.apworld} {apworld_label(apworld)}\n-# Envoyé sur le serveur par <@{apworld.uploaded_by}>{sent}"


def bulleted(lines: Callable[[str], list[str]], icon: str, budget: int) -> list[str]:
    """`lines(icon)`, or with plain dashes when the icons don't fit in `budget`."""
    full = lines(icon)
    return full if sum(len(line) + 1 for line in full) <= budget else lines("-")


def apworlds_section(apworlds: Sequence[Attached], missing: Sequence[str]) -> str:
    def lines(bullet: str) -> list[str]:
        return [f"{bullet} {apworld_label(a.apworld)} · <@{a.user_id}>" for a in apworlds] + [
            f"⚠️ *{md(game)}* · apworld manquant" for game in missing
        ]

    return f"### {E.apworld} Apworlds\n" + fit_lines(
        bulleted(lines, E.apworld, APWORLDS_BUDGET), APWORLDS_BUDGET, lambda hidden: f"… et {hidden} autres"
    )


def signup_roster(files: Sequence[YamlFile], budget: int) -> str:
    def lines(bullet: str) -> list[str]:
        return [
            line
            for f in files
            for line in (
                [f"⚠️ {code(f.filename)} · *yaml invalide* · <@{f.user_id}>"]
                if f.invalid
                else [f"{bullet} {player_label(p)} · {game_label(p)} · <@{f.user_id}>" for p in f.players]
            )
        ]

    return fit_lines(bulleted(lines, E.yaml, budget), budget, lambda hidden: f"… et {hidden} autres") or (
        "*Personne pour l'instant.*"
    )


def signup_view(
    game: GameRecord,
    files: Sequence[YamlFile],
    *,
    track_command: str = "`/track start`",
    ping_role_id: int | None = None,
    buttons: list[ui.Item] | None = None,
    apworlds: Sequence[Attached] = (),
    missing: Sequence[str] = (),
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

    steps = (
        "### Comment participer\n"
        f"**1.** Clique sur le bouton **{E.yaml} Mon yaml** pour **envoyer**, **modifier**, ou **retirer** ton "
        "fichier `.yaml` tant que la partie n'a pas commencé. Tu peux aussi le glisser dans ce post. Si ton jeu "
        "n'est pas officiel, ajoute son `.apworld` de la même façon.\n"
        f"**2.** Quand tout le monde est inscrit, l'organisateur récupère **{E.zip} Tous les yamls** (avec les "
        "apworlds) et génère la partie.\n"
        f"**3.** Il lance ensuite {track_command} ici avec le lien de la room : ce message devient le suivi de la "
        "partie et chacun est relié à son slot."
    )
    apworlds_text = apworlds_section(apworlds, missing) if apworlds or missing else ""

    slots = sum(len(f.players) for f in files)
    people = len({f.user_id for f in files})
    count = plural(people, "joueur") + (f" · {plural(slots, 'slot')}" if slots != people else "")
    title = f"### {E.signup} Inscrits" + (f" · {count}" if files else "") + "\n"
    budget = TEXT_LIMIT - len(header) - len(title) - len(apworlds_text) - (0 if cancelled else len(steps))
    view = ui.LayoutView(timeout=None)
    view.add_item(
        ui.Container(
            ui.TextDisplay(header),
            ui.Separator(),
            ui.TextDisplay(title + signup_roster(files, budget)),
            *([ui.Separator(), ui.TextDisplay(apworlds_text)] if apworlds_text else []),
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
    apworld: Apworld | None = None
    error: str | None = None
    invalid: str | None = None
    warnings: list[str] = field(default_factory=list)
    replaced: list[str] = field(default_factory=list)

    @property
    def clean(self) -> bool:
        return self.error is None and self.invalid is None and not self.warnings


def upload_text(upload: Upload) -> str:
    if upload.error:
        return f"{E.error} **{md(upload.filename)}** : {upload.error}"
    if upload.apworld:
        text = f"{E.apworld} **{md(upload.filename)}** : apworld {apworld_label(upload.apworld)}"
        if upload.replaced:
            text += "\n-# Remplace " + ", ".join(md(label) for label in upload.replaced)
        return text
    if upload.invalid:
        text = f"{E.yaml} **{md(upload.filename)}** : ⚠️ {upload.invalid}\n-# {INVALID_KEPT}"
    else:
        slots = ", ".join(f"{player_label(p)} · {game_label(p)}" for p in upload.players)
        text = f"{E.yaml} **{md(upload.filename)}** : {slots}"
    if upload.replaced:
        text += "\n-# Remplace " + ", ".join(f"{code(name)}" for name in upload.replaced)
    return text + "".join(f"\n-# ⚠️ {warning}" for warning in upload.warnings)


def upload_tone(uploads: Sequence[Upload], missing: Sequence[str]) -> Tone:
    failed = sum(u.error is not None for u in uploads)
    if uploads and failed == len(uploads):
        return Tone.ERROR
    invalid = any(u.invalid for u in uploads)
    return Tone.WARNING if failed or invalid or missing else Tone.SUCCESS
