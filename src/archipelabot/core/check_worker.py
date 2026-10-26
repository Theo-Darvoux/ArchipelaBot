"""Run by Archipelago's own Python, never imported by the bot: checks the yamls of Players/ against the worlds.

One JSON line in: {"archipelago", "workdir", "plando"}. One JSON line out: {"problems": [...]} or {"error": "..."}.
"""

import json
import os
import sys
import traceback

ALTTP_EXTRA = {"sprite_pool", "sprite", "random_sprite_on_event"}
MAX_DETAIL = 300


def main() -> None:
    out = os.fdopen(os.dup(1), "w", encoding="utf-8")
    os.dup2(2, 1)
    sys.stdout = sys.stderr
    job = json.loads(sys.stdin.readline())
    try:
        answer = {"problems": check(job)}
    except BaseException as e:
        traceback.print_exc()
        answer = {"error": f"{type(e).__name__}: {e}"[:1500]}
    out.write(json.dumps(answer) + "\n")
    out.flush()


def check(job: dict) -> list[dict]:
    os.environ["SKIP_REQUIREMENTS_UPDATE"] = "1"
    sys.path.insert(0, job["archipelago"])
    os.chdir(job["workdir"])
    import Utils

    Utils.user_path.cached_path = job["workdir"]  # type: ignore[attr-defined]
    os.rename("custom_worlds", "worlds")
    import Generate
    from BaseClasses import PlandoOptions
    from worlds.AutoWorld import AutoWorldRegister

    plando = PlandoOptions.from_option_string(job["plando"])
    problems = []
    folder = os.path.join(job["workdir"], "Players")
    for filename in sorted(os.listdir(folder)):
        try:
            documents = Generate.read_weights_yamls(os.path.join(folder, filename))
        except Exception as e:
            problems.append(problem(filename, None, None, "error", detail=reason(e)))
            continue
        for document in documents:
            if isinstance(document, dict):
                problems += check_player(filename, document, AutoWorldRegister.world_types, plando)
    return problems


def check_player(filename: str, document: dict, worlds: dict, plando) -> list[dict]:
    import Generate

    name = str(document.get("name", "?"))
    problems = []
    for game in candidates(document.get("game")):
        world, section = worlds.get(game), document.get(game)
        if world is None or not isinstance(section, dict):
            continue
        hints = world.options_dataclass.type_hints
        for key, value in section.items():
            if key == "triggers" or str(key)[:1] in "+-" or (game == "A Link to the Past" and key in ALTTP_EXTRA):
                continue
            option = hints.get(key)
            if option is None:
                problems.append(problem(filename, name, game, "warning", option=key))
                continue
            weighted = option.supports_weighting and isinstance(value, dict)
            for candidate in [v for v, w in value.items() if weight(w) > 0] if weighted else [value]:
                try:
                    option.from_any(candidate).verify(world, name, plando)
                except Exception as e:
                    detail = reason(e)
                    problems.append(problem(filename, name, game, "error", key, candidate, detail))
    if not problems:
        try:
            Generate.roll_settings(document, plando)
        except Exception as e:
            problems.append(problem(filename, name, None, "error", detail=reason(e)))
    return problems


def candidates(value) -> list[str]:
    if isinstance(value, dict):
        return [str(game) for game, w in value.items() if weight(w) > 0]
    return [value] if isinstance(value, str) else []


def weight(value) -> float:
    return value if isinstance(value, int | float) and not isinstance(value, bool) else 0


def reason(error: BaseException) -> str:
    """The deepest cause: Archipelago wraps option errors in "Error generating option…"."""
    while error.__cause__ is not None or (error.__context__ is not None and not error.__suppress_context__):
        error = error.__cause__ or error.__context__  # type: ignore[assignment]
    return (str(error) or type(error).__name__)[:MAX_DETAIL]


def problem(filename, player, game, level, option=None, value=None, detail="") -> dict:
    if value is not None and not isinstance(value, str | int | float | bool):
        value = json.dumps(value, default=str)
    return {
        "filename": filename, "player": player, "game": game, "level": level,
        "option": option, "value": value if value is None else str(value)[:100], "detail": detail,
    }  # fmt: skip


if __name__ == "__main__":
    main()
