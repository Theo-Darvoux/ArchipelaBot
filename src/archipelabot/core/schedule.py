import re
from datetime import date, datetime, time, timedelta

WEEKDAYS = ("lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche")
TIME = re.compile(r"(?<![\d/])(\d{1,2})\s*(?:h|:)\s*(\d{2})?(?!\d)")
DATE = re.compile(r"(?<!\d)(\d{1,2})/(\d{1,2})(?:/(\d{2}|\d{4}))?(?!\d)")
EXAMPLE = "par exemple `samedi 21h`, `demain 20h30` ou `12/10 21h`"
DEFAULT_HOUR = 21


class ScheduleError(ValueError):
    pass


def parse_start(text: str, now: datetime) -> datetime:
    """A start time typed in French, in `now`'s time zone."""
    lowered = text.strip().lower()
    clock = TIME.search(lowered)
    if clock is None:
        raise ScheduleError(f"Précise l'heure, {EXAMPLE}.")
    hour, minute = int(clock[1]), int(clock[2] or 0)
    if hour > 23 or minute > 59:
        raise ScheduleError(f"Heure invalide : `{clock[0].strip()}`.")
    at = time(hour, minute)
    today = now.date()

    if explicit := DATE.search(lowered):
        return _explicit_date(explicit, at, now)
    if "après-demain" in lowered or "apres-demain" in lowered:
        return _at(today + timedelta(days=2), at, now)
    if "demain" in lowered:
        return _at(today + timedelta(days=1), at, now)
    words = [w for w in re.findall(r"[a-zé]+", lowered) if len(w) >= 3]
    weekday = next((i for i, name in enumerate(WEEKDAYS) for w in words if name.startswith(w)), None)
    ahead = 0 if weekday is None else (weekday - today.weekday()) % 7
    start = _at(today + timedelta(days=ahead), at, now)
    if start > now:
        return start
    return start + timedelta(days=1 if weekday is None else 7)


def _explicit_date(match: re.Match[str], at: time, now: datetime) -> datetime:
    day, month, year = int(match[1]), int(match[2]), match[3]
    try:
        if year:
            start = _at(date(int(year) + (2000 if len(year) == 2 else 0), month, day), at, now)
        else:
            start = _at(date(now.year, month, day), at, now)
            if start < now - timedelta(days=1):
                start = _at(date(now.year + 1, month, day), at, now)
    except ValueError as e:
        raise ScheduleError(f"Date invalide : `{match[0]}`.") from e
    if start < now:
        raise ScheduleError("Cette date est déjà passée.")
    return start


def _at(day: date, at: time, now: datetime) -> datetime:
    return datetime.combine(day, at, tzinfo=now.tzinfo)


def label(start: datetime) -> str:
    return f"{WEEKDAYS[start.weekday()]} {start:%d/%m/%Y} à {start:%H:%M}"


def suggestions(text: str, now: datetime) -> list[datetime]:
    if text.strip():
        try:
            return [parse_start(text, now)]
        except ScheduleError:
            return []
    return sorted({parse_start(f"{day} {DEFAULT_HOUR}h", now) for day in ("", "demain", "samedi", "dimanche")})
