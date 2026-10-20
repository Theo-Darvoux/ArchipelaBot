"""Run by Archipelago's own Python, never imported by the bot: regenerates a seed, then answers logic questions.

Protocol, one JSON object per line. First line in: {"archipelago", "workdir", "seed", "plando"}; first line out:
{"ready": {...}} or {"error": "..."}. Then for each request in, one answer out.
"""

import hashlib
import json
import os
import sys
import traceback


def main() -> None:
    out = os.fdopen(os.dup(1), "w", encoding="utf-8")
    os.dup2(2, 1)  # worlds print to stdout: keep it for the protocol
    sys.stdout = sys.stderr

    def answer(data: dict) -> None:
        out.write(json.dumps(data) + "\n")
        out.flush()

    job = json.loads(sys.stdin.readline())
    try:
        multiworld, seed_name = generate(job)
    except BaseException as e:
        traceback.print_exc()
        answer({"error": f"{type(e).__name__}: {e}"[:1500]})
        return
    answer({"ready": describe(multiworld, seed_name)})
    for line in sys.stdin:
        request = json.loads(line)
        try:
            match request["cmd"]:
                case "evaluate":
                    answer({"slots": evaluate(multiworld, request["checked"])})
                case "placements":
                    answer({"placements": placements(multiworld, request["slot"])})
                case "spoiler":
                    answer({"spoiler": spoiler(multiworld)})
                case "place":
                    answer({"unmatched": place(multiworld, request["placements"])})
                case "inventory":
                    answer({"items": inventory(multiworld, request["checked"], request["slot"])})
                case other:
                    answer({"error": f"unknown command {other}"})
        except Exception as e:
            traceback.print_exc()
            answer({"error": f"{type(e).__name__}: {e}"[:1500]})


def generate(job: dict):
    os.environ["SKIP_REQUIREMENTS_UPDATE"] = "1"
    sys.path.insert(0, job["archipelago"])
    os.chdir(job["workdir"])
    import Utils

    # custom_worlds/ and host.yaml of this room only
    Utils.user_path.cached_path = job["workdir"]  # type: ignore[attr-defined]
    # a user folder that is not Archipelago's own keeps its apworlds in worlds/
    os.rename("custom_worlds", "worlds")
    import Generate
    import Main

    args = Generate.mystery_argparse(
        [
            "--player_files_path", os.path.join(job["workdir"], "Players"),
            "--outputpath", os.path.join(job["workdir"], "output"),
            "--seed", str(job["seed"]),
            "--plando", job["plando"],
            "--skip_output",
        ]
    )  # fmt: skip
    erargs, seed = Generate.main(args)
    return Main.main(erargs, seed), erargs.outputname


def describe(multiworld, seed_name: str) -> dict:
    import Utils

    fill = sorted(
        (loc.player, loc.address, loc.item.code, loc.item.player)
        for loc in multiworld.get_locations()
        if loc.address is not None and loc.item is not None and loc.item.code is not None
    )
    return {
        "seed_name": seed_name,
        "version": Utils.__version__,
        "fingerprint": hashlib.sha256(json.dumps(fill).encode()).hexdigest(),
        "slots": {
            player: {
                "name": multiworld.player_name[player],
                "game": multiworld.game[player],
                "locations": sum(1 for loc in multiworld.get_locations(player) if loc.address is not None),
            }
            for player in multiworld.player_ids
        },
    }


def evaluate(multiworld, checked: dict[str, list[int]]) -> dict[int, dict]:
    from BaseClasses import CollectionState

    done = {int(player): set(ids) for player, ids in checked.items()}
    state = CollectionState(multiworld)
    events = []
    for location in multiworld.get_locations():
        if location.address is None:
            events.append(location)
        elif location.item and location.address in done.get(location.player, ()) and location.item.advancement:
            for item in received(multiworld, location.item):
                state.collect(item, prevent_sweep=True)
    state.sweep_for_advancements(events)
    result = {}
    for player in multiworld.player_ids:
        checked_here = done.get(player, set())
        accessible = [
            loc
            for loc in multiworld.get_locations(player)
            if loc.address is not None and loc.address not in checked_here and loc.can_reach(state)
        ]
        result[player] = {
            "accessible": [loc.address for loc in accessible],
            "goal": bool(multiworld.completion_condition[player](state)),
            "unlocks": [
                item.player
                for loc in accessible
                if loc.item and loc.item.advancement
                for item in received(multiworld, loc.item)
                if item.player != player
            ],
        }
    return result


def inventory(multiworld, checked: dict[str, list[int]], player: int) -> list[list[str]]:
    """The progression items the logic counts for a player, with where each one comes from ("" for the start)."""
    done = {int(slot): set(ids) for slot, ids in checked.items()}
    items = [[item.name, ""] for item in multiworld.precollected_items[player] if item.advancement]
    for location in multiworld.get_locations():
        if location.address is None or not location.item or not location.item.advancement:
            continue
        if location.address in done.get(location.player, ()):
            source = f"{location.name} ({multiworld.player_name[location.player]})"
            items += [[item.name, source] for item in received(multiworld, location.item) if item.player == player]
    return items


def received(multiworld, item):
    group = multiworld.groups.get(item.player)
    if group is None:
        return [item]
    return [item] + [multiworld.create_item(item.name, member) for member in group["players"]]


def placements(multiworld, player: int) -> dict[int, list[int]]:
    return {
        loc.address: [loc.item.code, loc.item.player]
        for loc in multiworld.get_locations(player)
        if loc.address is not None and loc.item is not None and loc.item.code is not None
    }


def place(multiworld, placements: dict[str, dict[str, list[int]]]) -> int:
    """Put the room's items in the regenerated locations, for a seed whose fill can't be reproduced.

    Returns how many locations are only in the room or only in the regeneration.
    """
    locations = {(loc.player, loc.address): loc for loc in multiworld.get_locations() if loc.address is not None}
    unmatched = 0
    for player, items in placements.items():
        for address, (item_id, receiver) in items.items():
            location = locations.pop((int(player), int(address)), None)
            world = multiworld.worlds.get(receiver)
            if location is None or world is None or item_id not in world.item_id_to_name:
                unmatched += 1
                continue
            item = world.create_item(world.item_id_to_name[item_id])
            location.item, item.location = item, location
    return unmatched + len(locations)


def spoiler(multiworld) -> str:
    path = os.path.join("output", "regenerated_spoiler.txt")
    try:
        multiworld.spoiler.to_file(path)
    except Exception:
        traceback.print_exc()  # some worlds need the output stage: keep what was written before
    with open(path, encoding="utf-8-sig", errors="replace") as file:
        return file.read().split("\n\nPlaythrough:", 1)[0]


if __name__ == "__main__":
    main()
