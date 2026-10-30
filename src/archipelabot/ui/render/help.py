from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

import discord
from discord import ui

from ..components import Tone
from ..emojis import E

if TYPE_CHECKING:
    from ..types import Interaction


@dataclass(frozen=True, slots=True)
class HelpPage:
    label: str
    icon: str
    text: str


def help_pages(cmd: Callable[[str], str]) -> list[HelpPage]:
    home = (
        f"## {E.info} ArchipelaBot\n"
        "Le bot suit vos parties [Archipelago](https://archipelago.gg) sur Discord. Chaque room a son post dans le "
        "forum, avec le panneau de progression, le chat de la partie et les goals ; tous les checks vont dans son "
        "suivi complet, un post à part en lecture seule.\n"
        "### Pour commencer\n"
        f"**1.** Un admin choisit les forums avec {cmd('config')}.\n"
        f"**2.** Quelqu'un annonce la partie avec {cmd('partie nouvelle')}, et chacun y envoie son yaml.\n"
        f"**3.** {E.settings} **Gérer** puis **Lancer** (le bot génère et héberge), ou {cmd('track start')} avec le "
        "lien de la room.\n"
        f"**4.** Chacun clique sur {E.claim} **Je joue** sur le panneau.\n"
        "-# Les boutons ci-dessous détaillent chaque étape."
    )
    game = (
        f"## {E.signup} Organiser une partie\n"
        f"{cmd('partie nouvelle')} : annoncer la partie (nom, date, infos) ; le bot crée son post\n"
        f"{cmd('track start')} : suivre une room générée ailleurs, avec son lien\n"
        f"{cmd('apworld envoyer')} : ajouter un apworld au serveur hors d'une partie\n"
        "### Dans le post d'inscriptions\n"
        f"{E.yaml} **Mon yaml** (ou le fichier déposé dans le post) · {E.apworld} l'apworld si le jeu n'est pas "
        f"officiel · {E.zip} **Tous les yamls** : le zip pour générer\n"
        f"Pour l'organisateur et les modérateurs : {E.reachable} **Vérifier** teste chaque yaml avec les apworlds de "
        "la partie, comme à la génération (options invalides, options inconnues) · "
        f"{E.settings} **Gérer** : **Lancer** la partie (le bot la génère et l'{E.host} héberge, logique déjà "
        "active), la modifier, corriger les yamls de n'importe qui, ou l'annuler\n"
        f"-# Sans inscriptions, {cmd('track start')} crée directement le post de la room."
    )
    room = (
        f"## {E.chat} Pendant la partie\n"
        f"{cmd('claim')} · {cmd('unclaim')} : indiquer ou libérer ton slot (ou {E.claim} **Je joue**)\n"
        f"{cmd('status')} : ta progression détaillée, ou celle d'un autre joueur\n"
        f"{cmd('hints')} : tes hints, visibles seulement par toi\n"
        f"{cmd('historique')} : tout ce qui s'est passé dans la room, ou pour un joueur, avec export .txt\n"
        f"{cmd('recap')} : le récap de la room (podium, chiffres, graphique), visible seulement par toi\n"
        f"{cmd('blame')} : avec la logique, qui a en logique les items du plus de joueurs bloqués\n"
        f"-# Tu écris dans le post : ton message part dans le chat du jeu, et inversement.\n"
        f"### {E.notif_thread} Pings\n"
        f"Le bot ne mentionne personne pour les items, sauf ceux demandés avec {cmd('ping')} (vide : voir et "
        "retirer tes pings, et choisir de les recevoir en mention dans le post ou en DM)."
    )
    manage = (
        f"## {E.settings} Gérer une room\n"
        f"Le bouton {E.settings} **Gérer** du panneau ouvre :\n"
        "**Reconnecter** : relancer la connexion (ou changer le mot de passe)\n"
        f"**Logique** : avec le spoiler log, le panneau montre qui est {E.blocked} bloqué et combien de checks "
        "chacun peut faire\n"
        "**Apworlds** · **Yamls** : corriger après coup ceux qui ont servi à générer (partie lancée depuis ses "
        "inscriptions)\n"
        f"**Récap** : publier le récap (podium, chiffres, graphique), même après l'arrêt ; aussi avec {cmd('recap')}\n"
        "**Arrêter** : arrêter le suivi, ou revenir aux inscriptions après un mauvais lien\n"
        "Et ce qu'affiche le suivi complet, plus le pont de chat.\n"
        "-# Réservé à la personne qui a lancé le suivi et aux modérateurs."
    )
    admin = (
        f"## {E.host} Admins\n"
        f"{cmd('config')} ouvre toute la configuration, chaque réglage avec son menu :\n"
        "**Forum des parties** : où chaque partie a son post\n"
        "**Suivi complet** : le forum en lecture seule de tous les checks\n"
        "**Récaps** : un salon où publier aussi les récaps\n"
        "**Apworlds** : le salon d'apworlds, repris puis proposés dans les parties\n"
        "**Annonces** : le rôle pingé quand une partie est annoncée\n"
        "**Création des parties** : tous les membres, ou les admins seulement\n"
        "-# Réservé à la permission *Gérer le serveur*."
    )
    return [
        HelpPage("Accueil", "info", home),
        HelpPage("Organiser", "signup", game),
        HelpPage("En jeu", "chat", room),
        HelpPage("Gérer", "settings", manage),
        HelpPage("Admins", "host", admin),
    ]


class HelpView(ui.LayoutView):
    """One page at a time, chosen with a button per category."""

    def __init__(self, cmd: Callable[[str], str], page: int = 0) -> None:
        super().__init__(timeout=900)
        self.pages = help_pages(cmd)
        self.page = page
        self._build()

    def _build(self) -> None:
        self.clear_items()
        buttons = []
        for index, page in enumerate(self.pages):
            current = index == self.page
            button = ui.Button(
                label=page.label,
                emoji=E.partial(page.icon),
                style=discord.ButtonStyle.primary if current else discord.ButtonStyle.secondary,
                disabled=current,
            )
            button.callback = self._open(index)
            buttons.append(button)
        self.add_item(
            ui.Container(
                ui.TextDisplay(self.pages[self.page].text),
                ui.Separator(),
                ui.ActionRow(*buttons),
                accent_colour=Tone.INFO.value,
            )
        )

    def _open(self, index: int) -> Callable[["Interaction"], Awaitable[None]]:
        async def callback(interaction: "Interaction") -> None:
            self.page = index
            self._build()
            await interaction.response.edit_message(view=self)

        return callback
