from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from archipelabot.core.schedule import ScheduleError, label, parse_start, suggestions

PARIS = ZoneInfo("Europe/Paris")
NOW = datetime(2026, 10, 7, 18, 0, tzinfo=PARIS)  # a Wednesday


def at(*args) -> datetime:
    return datetime(*args, tzinfo=PARIS)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("21h", at(2026, 10, 7, 21)),
        ("ce soir à 20h30", at(2026, 10, 7, 20, 30)),
        ("9h", at(2026, 10, 8, 9)),
        ("demain 14:15", at(2026, 10, 8, 14, 15)),
        ("après-demain 21h", at(2026, 10, 9, 21)),
        ("Samedi 21h", at(2026, 10, 10, 21)),
        ("lundi 21h", at(2026, 10, 12, 21)),
        ("mercredi 17h", at(2026, 10, 14, 17)),
        ("mercredi 19h", at(2026, 10, 7, 19)),
        ("12/10 21h", at(2026, 10, 12, 21)),
        ("03/01 21h", at(2027, 1, 3, 21)),
        ("12/10/2026 21:00", at(2026, 10, 12, 21)),
        ("samedi 10/10/26 à 21:00", at(2026, 10, 10, 21)),
    ],
)
def test_parse_start(text, expected):
    assert parse_start(text, NOW) == expected


@pytest.mark.parametrize(
    ("text", "error"),
    [
        ("samedi", "Précise l'heure"),
        ("25h", "Heure invalide"),
        ("31/02 21h", "Date invalide"),
        ("07/10 10h", "déjà passée"),
        ("01/01/2020 21h", "déjà passée"),
    ],
)
def test_invalid_starts(text, error):
    with pytest.raises(ScheduleError, match=error):
        parse_start(text, NOW)


def test_labels_can_be_parsed_back():
    start = at(2026, 10, 10, 21)
    assert label(start) == "samedi 10/10/2026 à 21:00"
    assert parse_start(label(start), NOW) == start


def test_suggestions():
    days = [7, 8, 10, 11]
    assert suggestions("", NOW) == [at(2026, 10, day, 21) for day in days]
    assert suggestions("sam 21h", NOW) == [at(2026, 10, 10, 21)]
    assert suggestions("samedi", NOW) == []
