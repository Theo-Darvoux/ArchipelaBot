"""Hints, shown on demand (/hints, "Mes hints") to whoever asks."""

from collections import defaultdict
from collections.abc import Iterable

import discord
from discord import ui

from ...ap.protocol import HintStatus
from ...core.events import HintInfo
from ...core.room import RoomState
from .text import item_emoji, md, plural

HINT_COLOUR = discord.Colour(0xAF99EF)
TEXT_BUDGET = 3800

STATUS_ORDER = {
    HintStatus.PRIORITY: 0,
    HintStatus.UNSPECIFIED: 1,
    HintStatus.NO_PRIORITY: 2,
    HintStatus.AVOID: 3,
    HintStatus.FOUND: 4,
}
STATUS_SUFFIX = {
    HintStatus.PRIORITY: " · **prioritaire**",
    HintStatus.NO_PRIORITY: " · *pas prioritaire*",
    HintStatus.AVOID: " · *à éviter*",
}


def sort_hints(hints: Iterable[HintInfo]) -> list[HintInfo]:
    return sorted(hints, key=lambda h: (STATUS_ORDER[h.status], h.receiver, h.item))


def hint_line(hint: HintInfo, state: RoomState, *, show_finder: bool = False, show_receiver: bool = True) -> str:
    line = f"{item_emoji(hint.flags)} **{md(hint.item)}**"
    if show_receiver:
        line += f" pour {md(state.name(hint.receiver))}"
    if show_finder:
        line += f" chez {md(state.name(hint.finder))}"
    line += f" · *{md(hint.location)}*"
    if hint.entrance:
        line += f" (entrée : *{md(hint.entrance)}*)"
    return line + STATUS_SUFFIX.get(hint.status, "")


def _fit(sections: list[str]) -> str:
    """Join sections, cutting whole lines if the text doesn't fit in one message."""
    text = "\n\n".join(sections)
    if len(text) <= TEXT_BUDGET:
        return text
    kept = text[:TEXT_BUDGET].rsplit("\n", 1)[0]
    hidden = text.count("\n") - kept.count("\n")
    return f"{kept}\n-# … et {plural(hidden, 'autre ligne', 'autres lignes')}"


def _card(title: str, summary: str, body: str) -> ui.LayoutView:
    view = ui.LayoutView()
    view.add_item(
        ui.Container(
            ui.TextDisplay(f"### {title}\n-# {summary}"),
            ui.Separator(),
            ui.TextDisplay(body),
            accent_colour=HINT_COLOUR,
        )
    )
    return view


def player_hints_view(slots: list[int], state: RoomState, *, you: bool) -> ui.LayoutView:
    """What these slots must find in their world, and what they're waiting for from others."""
    pending = [h for h in state.hints.values() if not h.found]
    to_find = sort_hints(h for h in pending if h.finder in slots)
    waiting = sort_hints(h for h in pending if h.receiver in slots and h.finder not in slots)

    names = ", ".join(md(state.name(s)) for s in slots)
    title = f"Tes hints · {names}" if you else f"Hints de {names}"
    summary = f"{len(to_find)} à aller chercher · {len(waiting)} en attente"
    sections = []
    if to_find:
        heading = "À aller chercher dans ton monde" if you else "À aller chercher dans son monde"
        sections.append(f"**{heading}**\n" + "\n".join(hint_line(h, state) for h in to_find))
    if waiting:
        heading = "Ce que tu attends" if you else "Ce qu'on attend pour ce slot"
        lines = [hint_line(h, state, show_finder=True, show_receiver=False) for h in waiting]
        sections.append(f"**{heading}**\n" + "\n".join(lines))
    return _card(title, summary, _fit(sections) if sections else "*Aucun hint en attente.*")


def all_hints_view(state: RoomState) -> ui.LayoutView:
    """Every pending hint, grouped by the player who must go and find the item."""
    pending = [h for h in state.hints.values() if not h.found]
    by_finder: dict[int, list[HintInfo]] = defaultdict(list)
    for hint in pending:
        by_finder[hint.finder].append(hint)
    # Players with the most priority hints first: they're the ones others are waiting on.
    finders = sorted(
        by_finder, key=lambda s: (-sum(h.status == HintStatus.PRIORITY for h in by_finder[s]), -len(by_finder[s]))
    )
    sections = [
        f"**{md(state.name(finder))}** · *{md(state.game(finder))}* doit aller chercher\n"
        + "\n".join(hint_line(h, state) for h in sort_hints(by_finder[finder]))
        for finder in finders
    ]
    found = len(state.hints) - len(pending)
    summary = f"{plural(len(pending), 'hint à trouver', 'hints à trouver')} · {found} trouvés"
    return _card("Tous les hints", summary, _fit(sections) if sections else "*Aucun hint en attente.*")
