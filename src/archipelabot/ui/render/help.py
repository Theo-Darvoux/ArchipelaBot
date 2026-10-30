from collections.abc import Callable

from discord import ui

from ..components import Tone
from ..emojis import E


def help_view(cmd: Callable[[str], str]) -> ui.LayoutView:
    intro = (
        f"## {E.info} ArchipelaBot\n"
        "Le bot suit vos parties [Archipelago](https://archipelago.gg) sur Discord. Chaque room suivie a son post "
        "dans le forum, avec un panneau de progression de tous les joueurs et le chat de la partie ; tous les "
        "checks vont dans son suivi complet, un post à part en lecture seule."
    )
    start = (
        "### Pour commencer\n"
        f"**1.** Un admin choisit le salon forum avec {cmd('config forum')} (une seule fois).\n"
        f"**2.** Quelqu'un annonce la partie avec {cmd('partie nouvelle')} (nom, date de début, infos) : le bot crée "
        f"son post dans le forum ; {cmd('partie modifier')} pour changer ces infos, {cmd('partie annuler')} pour "
        f"l'annuler, {cmd('partie yamls')} pour corriger les yamls de n'importe qui, même après le lancement. "
        f"Chacun y envoie son yaml ({E.yaml} **Mon yaml**, ou en déposant le fichier dans le post), plus "
        f"l'{E.apworld} apworld si son jeu n'est pas officiel (ceux déjà envoyés sur le serveur sont proposés), et "
        f"{E.zip} **Tous les yamls** donne le zip pour générer, et {E.reachable} **Vérifier** teste chaque yaml "
        "avec les apworlds de la partie (organisateur et modérateurs).\n"
        f"**3.** Une fois la partie générée, lance {cmd('track start')} dans ce post avec le lien de la room : "
        "le suivi démarre et chacun est relié à son slot d'après son yaml. "
        f"Ou {cmd('partie lancer')} : le bot génère la partie et l'{E.host} héberge lui-même, avec la logique "
        f"déjà active, et chacun récupère son {E.patch} patch sur le panneau.\n"
        f"-# Sans inscriptions : {cmd('track start')} avec le lien de la room crée directement le post, et chacun "
        f"clique sur {E.claim} **Je joue**."
    )
    room = (
        f"### {E.chat} Dans le post d'une room\n"
        f"{cmd('status')} : ta progression détaillée, ou celle d'un autre joueur\n"
        f"{cmd('hints')} : tes hints, visibles seulement par toi\n"
        f"{cmd('claim')} · {cmd('unclaim')} : indiquer ou libérer ton slot\n"
        f"{cmd('ping')} : être prévenu quand ton slot reçoit un item précis (vide : voir et retirer tes pings)\n"
        f"{cmd('historique')} : tout ce qui s'est passé dans la room, ou pour un joueur, avec export .txt\n"
        f"{cmd('track reglages')} : choisir ce qui s'affiche dans le suivi complet\n"
        f"{cmd('track reconnect')} : relancer la connexion (ou changer le mot de passe)\n"
        f"{cmd('track logique')} : avec le spoiler log, le panneau montre qui est {E.blocked} bloqué et combien de "
        "checks chacun peut faire (partie lancée depuis son post d'inscriptions)\n"
        f"{cmd('blame')} : avec la logique, désigne qui a en logique les items du plus de joueurs bloqués\n"
        f"{cmd('track apworlds')} : choisir après coup les apworlds de la partie, parmi ceux envoyés au serveur "
        f"(dans les inscriptions ou avec {cmd('apworld envoyer')})\n"
        f"{cmd('recap')} : publier le récap (podium, chiffres, graphique), même après l'arrêt\n"
        f"{cmd('track stop')} : arrêter le suivi, ou revenir aux inscriptions après un mauvais lien\n"
        "-# Réglages, reconnexion, logique, apworlds, récap et arrêt : la personne qui a lancé le suivi ou un "
        "modérateur."
    )
    notifs = (
        f"### {E.notif_thread} Notifications\n"
        f"Le bot ne mentionne personne pour les items, sauf ceux demandés avec {cmd('ping')}. "
        f"{cmd('notifs')} pour recevoir ces pings en DM, ou plus du tout."
    )
    admin = (
        f"### {E.settings} Admins\n"
        f"{cmd('config forum')} · {cmd('config suivi')} · {cmd('config recap')} · {cmd('config annonces')} · "
        f"{cmd('config apworlds')} · {cmd('config creation')} · {cmd('config voir')}\n"
        f"-# {cmd('config suivi')} : le forum en lecture seule où chaque room a son suivi complet.\n"
        "-# Le salon d'apworlds : le bot reprend ceux qui y sont postés, et les propose ensuite dans les parties.\n"
        f"-# {cmd('config creation')} : réserver aux admins la création de parties, ou l'ouvrir à tous.\n"
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
