from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime

from ..core.progress import Progress
from ..core.room import RoomState
from ..storage.history import LoggedEvent, Snapshot


@dataclass(frozen=True, slots=True)
class Finisher:
    slot: int
    goal_at: datetime | None  # None: reached before the bot started tracking


@dataclass(frozen=True, slots=True)
class Series:
    slot: int
    points: list[tuple[datetime, float]]  # (time, completion ratio)
    goal_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class Recap:
    name: str
    started: datetime
    ended: datetime
    players: list[int]
    finishers: list[Finisher]
    unfinished: list[tuple[int, float | None]]
    checks_done: int
    checks_total: int | None
    hints: int
    deaths: int
    death_champion: tuple[int, int] | None  # (slot, deaths)
    releases: int
    collects: int
    series: list[Series] = field(default_factory=list)

    @property
    def everyone_finished(self) -> bool:
        return not self.unfinished


def build_recap(
    name: str,
    started: datetime,
    ended: datetime,
    state: RoomState,
    progress: Progress,
    events: list[LoggedEvent],
    snapshots: list[Snapshot],
) -> Recap:
    players = [s.slot for s in state.players]
    ranking = progress.ranking()
    finishers = [Finisher(slot, progress[slot].goal_at) for slot in ranking if progress.reached_goal(slot)]
    unfinished = [(slot, progress[slot].ratio) for slot in ranking if not progress.reached_goal(slot)]

    deaths = Counter(e.slot for e in events if e.kind == "death" and e.slot is not None)
    kinds = Counter(e.kind for e in events)
    champion = deaths.most_common(1)[0] if deaths else None

    return Recap(
        name=name,
        started=started,
        ended=ended,
        players=players,
        finishers=finishers,
        unfinished=unfinished,
        checks_done=progress.done,
        checks_total=progress.total,
        hints=kinds["hint"],
        deaths=sum(deaths.values()),
        death_champion=champion,
        releases=kinds["release"],
        collects=kinds["collect"],
        series=build_series(players, progress, snapshots, ended),
    )


def build_series(players: list[int], progress: Progress, snapshots: list[Snapshot], ended: datetime) -> list[Series]:
    """Completion over time for each player, from the snapshots, ending with the current values."""
    points: dict[int, list[tuple[datetime, float]]] = {slot: [] for slot in players}
    for snap in snapshots:
        total = snap.total or progress[snap.slot].total
        if snap.slot in points and total:
            points[snap.slot].append((snap.at, min(1.0, snap.checked / total)))
    series = []
    for slot in players:
        ratio = progress[slot].ratio
        if ratio is not None:
            points[slot].append((ended, ratio))
        if points[slot]:
            series.append(Series(slot, points[slot], progress[slot].goal_at))
    return series
