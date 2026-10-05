# ArchipelaBot

Bot Discord pour suivre des parties [Archipelago](https://archipelago.gg) : chaque room suivie a son post dans un
salon forum, avec un panneau de status, le fil des items, les événements importants et le pont de chat.

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
| `/config forum` · `recap` · `voir` | Configuration du serveur (permission *Gérer le serveur*) |
| `/track start lien:<room ou hôte:port>` | Suit une room : crée son post dans le forum |
| `/track reglages` | Dans le post d'une room : choisir ce qui est affiché |
| `/track stop` | Dans le post d'une room : arrêter le suivi, avec ou sans récap |
| `/claim slot:<joueur>` · `/unclaim` | Dans le post d'une room : dire quel slot tu joues (ou bouton « Je joue » du panneau) |
| `/status [joueur]` | Dans le post d'une room : progression détaillée d'un joueur (par défaut, le tien) |
| `/hints [joueur]` | Dans le post d'une room : tes hints (ou ceux d'un joueur), visibles seulement par toi |
| `/recap` | Dans le post d'une room : publier le récap (podium, chiffres, graphique) |
| `/notifs` | Être prévenu des items de progression reçus : mention dans le post, DM, ou rien. Seulement quand tu n'es pas en jeu (depuis au moins 2 minutes), et une seule fois par absence |

## Déploiement (VPS avec Docker)

Sur le serveur : Docker et le plugin Compose (`docker compose version`). Depuis ta machine :

1. **Première fois uniquement** : arrête le bot local (deux instances posteraient tout en double), puis copie la
   configuration et les données pour garder les rooms suivies, les claims et l'historique :
   ```sh
   ssh user@host mkdir -p archipelabot
   scp .env user@host:archipelabot/.env
   rsync -a data/ user@host:archipelabot/data/
   ```
2. **Déployer** (et à chaque mise à jour) : `scripts/deploy.sh user@host` copie le code, reconstruit l'image et
   relance le bot. Le `.env` et `data/` du serveur ne sont jamais écrasés.
3. **Logs** : `ssh user@host 'cd archipelabot && docker compose logs -f'`

Avec `DEV_GUILD_ID` dans le `.env`, les commandes ne sont disponibles que sur ce serveur Discord (mise à jour
instantanée). Sans, elles sont globales, mais Discord peut mettre jusqu'à une heure à les afficher.

## Pont de chat

Tout message écrit dans le post d'une room est relayé dans le chat de la partie. Si tu as indiqué ton slot
(« Je joue » ou `/claim`), il apparaît à ton nom : le bot ouvre pour ça une connexion de chat avec ton slot, annoncée
dans le jeu, et la ferme après une heure sans message. Sinon, il apparaît au nom du slot du bot, signé
`[Discord] Pseudo:`. La réaction du bot indique s'il est parti, s'il a été bloqué (les commandes `!` ne sont jamais
relayées) ou si le bot n'est pas connecté. Les messages du chat de la partie apparaissent dans le fil. Désactivable
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
