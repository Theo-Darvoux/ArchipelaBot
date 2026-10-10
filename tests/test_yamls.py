import pytest

from archipelabot.core.room import SlotInfo
from archipelabot.core.yamls import (
    PlayerYaml,
    YamlError,
    YamlFile,
    YamlSyntaxError,
    assign_slots,
    parse_players,
    read_yaml,
    replaced_by,
    warnings,
)

LUIGI = b"""
name: Psders1
description: test
game: Luigi's Mansion
Luigi's Mansion:
  boo_gates: true
"""


def test_reads_name_and_game():
    assert parse_players(LUIGI) == [PlayerYaml("Psders1", "Luigi's Mansion")]


def test_several_documents_are_several_slots():
    content = b"name: A\ngame: Celeste\n---\nname: B\ngame: Terraria\n---\n"
    assert [p.name for p in parse_players(content)] == ["A", "B"]


def test_weighted_games():
    assert parse_players(b"name: A\ngame:\n  Celeste: 1\n  Terraria: 0\n")[0].game == "Celeste"
    assert parse_players(b"name: A\ngame:\n  Celeste: 1\n  Terraria: 2\n")[0].game is None


def test_byte_order_mark_is_ignored():
    assert parse_players(b"\xef\xbb\xbf" + LUIGI)[0].name == "Psders1"


@pytest.mark.parametrize(
    ("content", "error"),
    [
        (b"", "vide"),
        (b"- a\n- b\n", "pas un yaml de joueur"),
        (b"game: Celeste\n", "nom du slot"),
        (b"name: A\n", "jeu"),
        (b"name: Archipelago\ngame: Celeste\n", "réservé"),
        (b"name: \xff\ngame: x\n", "UTF-8"),
        (b"name: A\ngame: Celeste\n" + b"#" * 600_000, "trop gros"),
    ],
)
def test_invalid_yamls(content, error):
    with pytest.raises(YamlError, match=error):
        parse_players(content)


def test_syntax_errors_say_where_and_what():
    with pytest.raises(YamlSyntaxError) as e:
        parse_players(b"name: A\ngame: Celeste\n  Celeste: 1\n")
    assert str(e.value) == "Yaml invalide ligne 3, colonne 10 : `mapping values are not allowed in this context`"


def test_a_syntax_error_does_not_block():
    f = read_yaml(1, "a.yaml", b"name: A\ngame: [\n")
    assert f.players == [] and f.invalid.startswith("Yaml invalide ligne 3")
    assert read_yaml(1, "a.yaml", LUIGI).invalid is None
    with pytest.raises(YamlError, match="nom du slot"):
        read_yaml(1, "a.yaml", b"game: Celeste\n")


def test_placeholders_and_long_names():
    templated = PlayerYaml("Leo{number}", "Celeste")
    assert templated.templated and templated.final_name is None
    long = PlayerYaml("AVeryLongPlayerName", "Celeste")
    assert long.final_name == "AVeryLongPlayerN"
    assert warnings([templated, long]) == [
        "`AVeryLongPlayerName` fait plus de 16 caractères : il deviendra `AVeryLongPlayerN`."
    ]


def yaml_file(user, filename, *players, id=None):
    return YamlFile(user, filename, b"", [PlayerYaml(name, game) for name, game in players], id=id)


def test_a_slot_name_belongs_to_one_person():
    files = [yaml_file(1, "a.yaml", ("Psders", "Celeste"))]
    with pytest.raises(YamlError, match=r"\*\*Psders\*\* est déjà pris par <@1>"):
        replaced_by(files, yaml_file(2, "b.yaml", ("psders", "Terraria")))
    with pytest.raises(YamlError, match="deux fois"):
        replaced_by([], yaml_file(2, "b.yaml", ("Leo", "Celeste"), ("LEO", "Terraria")))
    # Placeholders are numbered by the generator: no clash.
    assert replaced_by([yaml_file(1, "a.yaml", ("P{number}", "x"))], yaml_file(2, "b.yaml", ("P{number}", "x"))) == []


def test_new_versions_replace_the_old_ones():
    first = yaml_file(1, "celeste.yaml", ("Psders", "Celeste"))
    second = yaml_file(1, "terraria.yaml", ("Psders2", "Terraria"))
    templated = yaml_file(1, "leo.yaml", ("Leo{number}", "Celeste"))
    files = [first, second, templated]
    assert replaced_by(files, yaml_file(1, "new.yaml", ("Psders", "Hollow Knight"))) == [first]
    assert replaced_by(files, yaml_file(1, "leo.yaml", ("Leo{player}", "Celeste"))) == [templated]
    assert replaced_by(files, yaml_file(1, "other.yaml", ("Other", "x"))) == []


def test_slots_are_assigned_to_who_sent_the_yaml():
    files = [
        yaml_file(1, "a.yaml", ("Psders1", "Luigi's Mansion")),
        yaml_file(2, "b.yaml", ("Leo{number}", "Celeste"), ("Random", None)),
        yaml_file(3, "c.yaml", ("P{number}", "Terraria")),
        yaml_file(4, "d.yaml", ("P{number}", "Terraria")),
    ]
    players = [
        SlotInfo(1, "PSDERS1", "Luigi's Mansion"),
        SlotInfo(2, "Leo1", "Celeste"),
        SlotInfo(3, "Random", "ANIMAL WELL"),
        SlotInfo(4, "P1", "Terraria"),
        SlotInfo(5, "P2", "Terraria"),
        SlotInfo(6, "Leo2", "Minecraft"),
    ]
    assert assign_slots(files, players) == {1: 1, 2: 2, 3: 2}
