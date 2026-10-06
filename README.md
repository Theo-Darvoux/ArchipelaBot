# ArchipelaBot

Bot Discord pour organiser et suivre des parties [Archipelago](https://archipelago.gg) : chaque partie a son post
dans un salon forum, de l'annonce (inscriptions et yamls) jusqu'à la fin, avec un panneau de status, le fil des items,
les événements importants et le pont de chat.

## Installation

1. Sur le [Discord Developer Portal](https://discord.com/developers/applications), crée une application, puis dans
   **Bot** :
   - récupère le token (*Reset Token*) ;
   - active **Message Content Intent** (nécessaire au pont de chat).
2. Invite le bot sur ton serveur (remplace `CLIENT_ID`) :
   `https://discord.com/oauth2/authorize?client_id=CLIENT_ID&scope=bot+applications.commands&permissions=328565115984`
3. Configure et lance :
   ```sh
   cp .env.example .env   # puis remplis DISCORD_TOKEN (et DEV_GUILD_ID pour un serveur de test)
   uv run archipelabot
   ```
4. Sur Discord, crée un salon **forum** puis lance `/config forum salon:#ton-forum`.

## Commandes

| Commande | Effet |
|---|---|
| `/help` | Comment fonctionne le bot et ce que fait chaque commande |
| `/config forum` · `recap` · `annonces` · `voir` | Configuration du serveur (permission *Gérer le serveur*). `annonces` : le rôle pingé quand une partie est annoncée et quand sa room ouvre |
| `/partie nouvelle [nom] [description]` | Annonce une partie : crée son post dans le forum, avec les inscriptions ouvertes (voir plus bas) |
| `/track start lien:<room ou hôte:port>` | Suit une room : crée son post dans le forum. Dans le post d'une partie en inscriptions, la suit dans ce post |
| `/track reglages` | Dans le post d'une room : choisir ce qui est affiché |
| `/track reconnect [mot_de_passe]` | Dans le post d'une room : relancer la connexion tout de suite (par exemple après un refus du serveur), en changeant le mot de passe si besoin |
| `/track stop` | Dans le post d'une room : arrêter le suivi, avec ou sans récap |
| `/claim slot:<joueur>` · `/unclaim` | Dans le post d'une room : dire quel slot tu joues (ou bouton « Je joue » du panneau) |
| `/status [joueur]` | Dans le post d'une room : progression détaillée d'un joueur (par défaut, le tien) |
| `/hints [joueur]` | Dans le post d'une room : tes hints (ou ceux d'un joueur), visibles seulement par toi |
| `/recap` | Dans le post d'une room : publier le récap (podium, chiffres, graphique), même une fois le suivi arrêté. Réservé à qui a lancé le suivi et aux modérateurs |
| `/notifs` | Être prévenu des items de progression reçus : mention dans le post, DM, ou rien. Seulement quand tu n'es pas en jeu (depuis au moins 2 minutes), et une seule fois par absence |

## Déploiement (VPS avec Docker)

Sur le serveur : Docker et le plugin Compose (`docker compose version`). Arrête le bot local avant (deux instances
posteraient tout en double).

1. **Première fois** :
   ```sh
   git clone https://github.com/Theo-Darvoux/ArchipelaBot.git && cd ArchipelaBot
   cp .env.example .env   # puis remplis DISCORD_TOKEN
   mkdir -p data && sudo chown 1000:1000 data   # le bot tourne sous l'utilisateur 1000 du conteneur
   docker compose up -d --build
   ```
2. **Mise à jour** : `git pull && docker compose up -d --build`. Le `.env` et `data/` ne sont jamais touchés.
   Si `data/` a été créé par une version qui tournait en root : `sudo chown -R 1000:1000 data` une fois avant.
3. **Logs** : `docker compose logs -f`
4. **Sauvegardes** : le bot copie sa base chaque jour dans `data/backups/` (7 jours gardés). Pour restaurer :
   arrête le bot, remplace `data/archipelabot.db` par une copie (et supprime `archipelabot.db-wal` / `-shm`), relance.

Avec `DEV_GUILD_ID` dans le `.env`, les commandes ne sont disponibles que sur ce serveur Discord (mise à jour
instantanée). Sans, elles sont globales, mais Discord peut mettre jusqu'à une heure à les afficher.

## Une partie, un post

1. `/partie nouvelle` crée le post avec le tag *Inscriptions* et pinge le rôle choisi avec `/config annonces`.
2. Chacun envoie son yaml avec le bouton **Mon yaml** (plusieurs fichiers possibles) ou en le déposant dans le post.
   Le bot lit le nom du slot et le jeu, refuse un nom déjà pris par quelqu'un d'autre, et prévient si un nom dépasse
   16 caractères (Archipelago le coupe). Renvoyer un yaml pour le même slot remplace l'ancien. **Tous les yamls**
   donne un zip à mettre dans `Players/` pour générer.
3. Quand la room est prête, n'importe qui colle son lien (`archipelago.gg/room/…`) dans le post : le panneau devient
   celui de la room, le tag passe à *En cours*, chaque slot est attribué à qui a envoyé son yaml (les noms avec
   `{number}` ou `{player}` quand il n'y a pas d'ambiguïté), et le bot pinge le rôle et les joueurs inscrits. Pour
   une room avec mot de passe, utilise `/track start` dans le post.

## Pont de chat

Tout message écrit dans le post d'une room est relayé dans le chat de la partie. Si tu as indiqué ton slot
(« Je joue » ou `/claim`), il apparaît à ton nom : le bot ouvre pour ça une connexion de chat avec ton slot, annoncée
dans le jeu, et la ferme après une heure sans message. Sinon, il apparaît au nom du slot du bot, signé
`[Discord] Pseudo:`. Le bot réagit au message s'il a été bloqué (les commandes `!` ne sont jamais relayées) ou
s'il n'a pas pu partir (bot pas connecté à la room). Les messages du chat de la partie apparaissent dans le fil. Désactivable
dans `/track reglages`.

## Développement

```sh
uv sync
scripts/setup-dev-server.sh   # serveur Archipelago local + seed de test (pour les tests d'intégration)
uv run pytest
uv run ruff check && uv run ruff format
```

Organisation du code :

- `ap/` : protocole Archipelago (client WebSocket, datapackage, API archipelago.gg). Ne connaît pas Discord.
- `core/` : `RoomTracker` suit une room, gère la reconnexion et transforme les paquets en événements.
- `ui/` : tout ce qui touche Discord (cogs, rendu Components V2, services de fil et de panneau).
- `storage/` : SQLite, avec migrations dans `storage/db.py`.
