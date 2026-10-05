from collections.abc import Callable

from discord import ui

from ..components import Tone
from ..emojis import E


def help_view(cmd: Callable[[str], str]) -> ui.LayoutView:
    intro = (
        f"## {E.info} ArchipelaBot\n"
        "Le bot suit vos parties [Archipelago](https://archipelago.gg) sur Discord. Chaque room suivie a son post "
        "dans le forum, avec un panneau de progression de tous les joueurs, le fil des items envoyés et le chat "
        "de la partie."
    )
    start = (
        "### Pour commencer\n"
        f"**1.** Un admin choisit le salon forum avec {cmd('config forum')} (une seule fois).\n"
        f"**2.** Quelqu'un lance {cmd('track start')} avec le lien de la room (`archipelago.gg/room/…`). "
        "Le bot crée son post dans le forum.\n"
        f"**3.** Dans ce post, chacun clique sur {E.claim} **Je joue** pour indiquer son slot."
    )
    room = (
        f"### {E.chat} Dans le post d'une room\n"
        f"{cmd('status')} : ta progression détaillée, ou celle d'un autre joueur\n"
        f"{cmd('hints')} : tes hints, visibles seulement par toi\n"
        f"{cmd('claim')} · {cmd('unclaim')} : indiquer ou libérer ton slot\n"
        f"{cmd('track reglages')} : choisir ce qui s'affiche dans le fil\n"
        f"{cmd('recap')} : publier le récap (podium, chiffres, graphique)\n"
        f"{cmd('track stop')} : arrêter le suivi"
    )
    notifs = (
        f"### {E.notif_thread} Notifications\n"
        "Si tu reçois un item de progression pendant que tu n'es pas en jeu, le bot te mentionne dans le post, "
        f"une fois par absence. {cmd('notifs')} pour recevoir ça en DM, ou plus du tout."
    )
    admin = (
        f"### {E.settings} Admins\n"
        f"{cmd('config forum')} · {cmd('config recap')} · {cmd('config voir')}\n"
        "-# Réservé à la permission *Gérer le serveur*."
    )

    view = ui.LayoutView()
    view.add_item(
        ui.Container(
            ui.TextDisplay(intro),
            ui.TextDisplay(start),
            ui.Separator(),
            ui.TextDisplay(room),
            ui.Separator(),
            ui.TextDisplay(notifs),
            ui.Separator(),
            ui.TextDisplay(admin),
            accent_colour=Tone.INFO.value,
        )
    )
    return view
