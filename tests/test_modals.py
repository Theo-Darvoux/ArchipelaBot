from typing import Any

from archipelabot.ui.manage import LogicModal


def label_descriptions(payload: dict) -> list[str]:
    found = []
    for component in payload["components"]:
        if "description" in component:
            found.append(component["description"])
    return found


async def test_logic_modal_label_descriptions_fit_discord_limit():
    runtime: Any = None
    descriptions = label_descriptions(LogicModal(runtime).to_dict())

    assert descriptions
    assert all(1 <= len(d) <= 100 for d in descriptions)
