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
        f"**2.** Quelqu'un annonce la partie avec {cmd('partie nouvelle')} (nom, date de début, infos) : le bot crée "
        f"son post dans le forum ; {cmd('partie modifier')} pour changer ces infos, {cmd('partie annuler')} pour "
        "l'annuler. "
        f"Chacun y envoie son yaml ({E.yaml} **Mon yaml**, ou en déposant le fichier dans le post), et "
        f"{E.zip} **Tous les yamls** donne le zip pour générer.\n"
        f"**3.** Une fois la partie générée, lance {cmd('track start')} dans ce post avec le lien de la room : "
        "le suivi démarre et chacun est relié à son slot d'après son yaml.\n"
        f"-# Sans inscriptions : {cmd('track start')} avec le lien de la room crée directement le post, et chacun "
        f"clique sur {E.claim} **Je joue**."
    )
    room = (
        f"### {E.chat} Dans le post d'une room\n"
        f"{cmd('status')} : ta progression détaillée, ou celle d'un autre joueur\n"
        f"{cmd('hints')} : tes hints, visibles seulement par toi\n"
        f"{cmd('claim')} · {cmd('unclaim')} : indiquer ou libérer ton slot\n"
        f"{cmd('track reglages')} : choisir ce qui s'affiche dans le fil\n"
        f"{cmd('track reconnect')} : relancer la connexion (ou changer le mot de passe)\n"
        f"{cmd('recap')} : publier le récap (podium, chiffres, graphique), même après l'arrêt\n"
        f"{cmd('track stop')} : arrêter le suivi, ou revenir aux inscriptions après un mauvais lien\n"
        "-# Réglages, reconnexion, récap et arrêt : la personne qui a lancé le suivi ou un modérateur."
    )
    notifs = (
        f"### {E.notif_thread} Notifications\n"
        "Si tu reçois un item de progression pendant que tu n'es pas en jeu, le bot te mentionne dans le post, "
        f"une fois par absence. {cmd('notifs')} pour recevoir ça en DM, ou plus du tout."
    )
    admin = (
        f"### {E.settings} Admins\n"
        f"{cmd('config forum')} · {cmd('config recap')} · {cmd('config annonces')} · {cmd('config voir')}\n"
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
