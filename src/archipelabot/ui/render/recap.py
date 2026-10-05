from datetime import timedelta

import discord
from discord import ui

from ...core.room import RoomState
from ...recap.stats import Recap
from ..emojis import E
from .text import md, plural

RECAP_COLOUR = discord.Colour(0xF0B232)
CHART_FILENAME = "recap.png"
MEDALS = ["🥇", "🥈", "🥉"]


def duration(delta: timedelta) -> str:
    minutes = int(delta.total_seconds() // 60)
    days, minutes = divmod(minutes, 24 * 60)
    hours, minutes = divmod(minutes, 60)
    parts = [f"{days} j"] if days else []
    if hours or days:
        parts.append(f"{hours} h")
    if not days:
        parts.append(f"{minutes:02} min" if hours else f"{minutes} min")
    return " ".join(parts)


def podium(recap: Recap, state: RoomState) -> str:
    lines = []
    for rank, finisher in enumerate(recap.finishers, start=1):
        place = MEDALS[rank - 1] if rank <= len(MEDALS) else f"**{rank}.**"
        when = f"en {duration(finisher.goal_at - recap.started)}" if finisher.goal_at else "avant le début du suivi"
        lines.append(f"{place} **{md(state.name(finisher.slot))}** · *{md(state.game(finisher.slot))}* · {when}")
    for slot, ratio in recap.unfinished:
        percent = "?" if ratio is None else f"{int(ratio * 100)}"
        lines.append(f"{E.ring(ratio)} {md(state.name(slot))} · *{md(state.game(slot))}* · {percent} %")
    return "\n".join(lines)


def numbers(recap: Recap, state: RoomState) -> str:
    total = recap.checks_total
    checks = (
        f"{recap.checks_done} / {total} checks ({int(recap.checks_done / total * 100)} %)"
        if total
        else (plural(recap.checks_done, "check"))
    )
    lines = [f"{E.prog} {checks}", f"{E.hint} {plural(recap.hints, 'hint')}"]
    if recap.deaths:
        line = f"{E.death} {plural(recap.deaths, 'mort')}"
        if recap.death_champion:
            slot, count = recap.death_champion
            line += f" · record : **{md(state.name(slot))}** ({count})"
        lines.append(line)
    if recap.releases or recap.collects:
        lines.append(
            f"{E.release} {plural(recap.releases, 'release')} · {E.collect} {plural(recap.collects, 'collect')}"
        )
    return "\n".join(lines)


def recap_view(recap: Recap, state: RoomState, *, chart: bool, thread_url: str | None = None) -> ui.LayoutView:
    title = "Partie terminée !" if recap.everyone_finished else "Récap"
    subtitle = (
        f"{md(recap.name)} · {plural(len(recap.players), 'joueur')} · "
        f"suivie pendant {duration(recap.ended - recap.started)}"
    )
    if thread_url:
        subtitle += f" · [voir la room]({thread_url})"

    items: list[ui.Item] = [
        ui.TextDisplay(f"## {E.trophy} {title}\n-# {subtitle}"),
        ui.Separator(),
        ui.TextDisplay(podium(recap, state)),
        ui.Separator(),
        ui.TextDisplay(numbers(recap, state)),
    ]
    if chart:
        items.append(ui.MediaGallery(discord.MediaGalleryItem(f"attachment://{CHART_FILENAME}")))
        items.append(ui.TextDisplay("-# Progression depuis le début du suivi · ★ objectif atteint"))

    view = ui.LayoutView()
    view.add_item(ui.Container(*items, accent_colour=RECAP_COLOUR))
    return view
