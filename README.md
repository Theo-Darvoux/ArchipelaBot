# ArchipelaBot

Bot Discord pour organiser et suivre des parties [Archipelago](https://archipelago.gg) : chaque partie a son post
dans un salon forum, de l'annonce (inscriptions et yamls) jusqu'à la fin, avec un panneau de status, les goals et le
pont de chat. Tous les checks vont dans un post à part, dans un forum de suivi complet en lecture seule.

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
4. Sur Discord, crée un salon **forum** pour les parties et un second forum pour le suivi complet (par exemple
   `#suivi-complet`, en retirant aux membres *Envoyer des messages* et *Envoyer des messages dans les fils*), puis
   choisis-les avec `/config`.

## Commandes

| Commande | Effet |
|---|---|
| `/help` | Comment fonctionne le bot et ce que fait chaque commande |
| `/config` | Toute la configuration du serveur sur un panneau, chaque réglage avec son menu (permission *Gérer le serveur*). **Forum des parties** : où chaque partie a son post. **Suivi complet** : le forum où chaque room a son suivi complet (items, morts, hints, release…), le post de la room ne gardant que le chat, les goals et les pings. **Récaps** : un salon où publier aussi les récaps. **Apworlds** : le salon texte où vous postez vos apworlds ; le bot reprend tout son historique, puis chaque nouvel apworld posté (✅ en réaction), pour les proposer dans les parties. **Annonces** : le rôle pingé quand une partie est annoncée et quand sa room ouvre. **Création des parties** : tous les membres ou les admins seulement |
| Bouton **Gérer** du post d'inscriptions | **Modifier** la partie (nom, date, description), corriger les **Yamls** de n'importe qui (un yaml envoyé remplace celui du même slot, et reste à son propriétaire), ou **Annuler la partie** (le post reste, avec le tag *Annulée*). Réservé à qui l'a annoncée et aux modérateurs |
| Bouton **Gérer** du panneau d'une room | **Reconnecter** (en changeant le mot de passe si besoin), **Logique** (envoyer le spoiler log de la génération : le panneau et `/status` montrent qui est bloqué et combien de checks chacun peut faire d'après la logique, voir plus bas), **Apworlds** et **Yamls** (room lancée depuis une partie : corriger après coup ceux qui ont servi à générer, puis renvoyer le spoiler avec **Logique**), **Récap** (podium, chiffres, graphique, même une fois le suivi arrêté), **Arrêter** (avec ou sans récap, ou retour aux inscriptions après un mauvais lien), et ce qu'affiche le suivi complet. Réservé à qui a lancé le suivi et aux modérateurs |
| `/partie nouvelle [nom] [debut] [description]` | Annonce une partie : crée son post dans le forum, avec les inscriptions ouvertes (voir plus bas). `debut` : « samedi 21h », « demain 20h30 », « 12/10 21h »… |
| `/partie lancer [release] [collect] [remaining] [cout_hint] [mot_de_passe]` | Dans le post d'une partie en inscriptions : le bot génère la partie avec les yamls et apworlds du post, l'héberge lui-même et la suit dans ce post (voir plus bas). Réservé à qui l'a annoncée et aux modérateurs |
| `/track start lien:<room ou hôte:port>` | Suit une room : crée son post dans le forum. Dans le post d'une partie en inscriptions, la suit dans ce post |
| `/recap` | Dans le post d'une room, même une fois le suivi arrêté : le récap (podium, chiffres, graphique). Qui a lancé le suivi et les modérateurs le publient dans le post ; pour les autres, il s'affiche en privé |
| `/apworld envoyer fichier:<.apworld>` | Ajouter un apworld au serveur hors d'une partie : il est ensuite proposé dans les inscriptions et dans **Gérer** puis **Apworlds** |
| `/claim slot:<joueur>` · `/unclaim` | Dans le post d'une room : dire quel slot tu joues (ou bouton « Je joue » du panneau) |
| `/status [joueur]` | Dans le post d'une room : progression détaillée d'un joueur (par défaut, le tien) |
| `/hints [joueur]` | Dans le post d'une room : tes hints (ou ceux d'un joueur), visibles seulement par toi |
| `/ping [item] [joueur]` | Dans le post d'une room : être prévenu chaque fois que ton slot (ou celui de `joueur`) reçoit cet item. Sans `item` : voir et retirer tes pings, et choisir de les recevoir en mention dans le post ou en DM |
| `/historique [joueur]` | Dans le post d'une room ou son suivi complet, même après l'arrêt : tout ce qui s'est passé (items, morts, goals, hints, release…), ou seulement ce qui concerne un joueur, page par page, avec export `.txt` |

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

1. `/partie nouvelle` crée le post avec le tag *Inscriptions* et pinge le rôle choisi dans `/config` (**Annonces**). La
   date de début s'affiche à l'heure locale de chacun.
2. Chacun envoie son yaml avec le bouton **Mon yaml** (plusieurs fichiers possibles) ou en le déposant dans le post.
   Le bot lit le nom du slot et le jeu, refuse un nom déjà pris par quelqu'un d'autre, et prévient si un nom dépasse
   16 caractères (Archipelago le coupe). Renvoyer un yaml pour le même slot remplace l'ancien. **Tous les yamls**
   donne un zip à mettre dans `Players/` pour générer.

   Si un yaml demande une version d'apworld (`requires: game:`) que l'apworld de la partie n'a pas, le bot le
   signale à l'envoi et sur le panneau, d'après le manifeste de l'apworld (sans exécuter son code). **Vérifier**
   (avec Archipelago installé sur le bot, réservé à qui a annoncé la partie et aux modérateurs) va plus loin :
   Archipelago charge les apworlds et teste chaque option de chaque yaml, avec toutes les valeurs que leurs poids
   peuvent tirer. Il signale les valeurs refusées (la génération échouerait) et les options que l'apworld ne
   connaît pas (souvent un yaml fait pour une autre version). Cette vérification exécute le code des apworlds du
   post sur la machine du bot.
3. Quand la room est prête, lance `/track start` dans le post avec son lien : le panneau devient celui de la room,
   le tag passe à *En cours*, chaque slot est attribué à qui a envoyé son yaml (les noms avec `{number}` ou
   `{player}` quand il n'y a pas d'ambiguïté), et le bot pinge le rôle et les joueurs inscrits. Un lien collé dans
   le post ne lance rien : pas de risque de suivre la mauvaise room.

Le bot supprime le message « a épinglé un message » qui suit l'épinglage du panneau.

## Héberger une partie

À la place des étapes 2 et 3, `/partie lancer` dans le post : le bot génère la partie avec Archipelago (la version
installée la plus récente), à partir des yamls et apworlds du post, puis lance son serveur sur sa propre machine.
Le post devient le suivi de la room, comme avec `/track start`, avec en plus :

- l'adresse pour jouer, `HOST_ADDRESS:port`, dans l'annonce et sur le panneau ;
- **Mes patchs** sur le panneau, pour les jeux qui ont besoin d'un fichier (patch de ROM, mod…) : chacun reçoit,
  visible seulement par lui, les fichiers des slots qu'il joue (ou choisit un slot s'il n'en a pas) ;
- les bloqués et checks en logique, actifs tout de suite (le bot a la seed, pas besoin de **Logique**).

Les options de `/partie lancer` sont celles du serveur : `release` et `collect` (par défaut automatiques à
l'objectif), `remaining` (après l'objectif), le coût des hints (10 %) et un mot de passe. Le serveur sauvegarde la
partie dans `data/hosted/`, redémarre avec le bot et s'il plante, et s'arrête avec le suivi (**Gérer** puis **Arrêter**, fin de
partie, ou 7 jours sans check). Revenir aux inscriptions supprime la seed. Avant de générer, `/partie lancer` fait
la même vérification que **Vérifier** et refuse de lancer s'il reste une valeur refusée.

Côté bot : Archipelago installé (comme pour la logique), `HOST_ADDRESS` dans le `.env` (le nom ou l'IP de la machine
pour les joueurs), et les ports de `HOST_PORTS` (par défaut `38281-38300`, une room par port) ouverts : publiés par
`compose.yaml`, et autorisés par le pare-feu de la machine. Comme la logique, la génération exécute le code des
apworlds du post sur la machine du bot.

## Bloqués et checks en logique

Le serveur Archipelago ne connaît pas la logique des jeux : pour savoir à coup sûr qui est bloqué, le bot regénère
la seed avec Archipelago, à partir des yamls et apworlds du post et de la seed indiquée dans le spoiler log
(`AP_…_Spoiler.txt`, ou le zip `AP_….zip` qui le contient), envoyé avec **Gérer** puis **Logique** (réponse visible seulement
par toi : le spoiler reste privé). Il vérifie que la regénération donne exactement les mêmes items que la room, puis
après chaque check calcule, avec les items reçus par chacun :

- 🔓 **N checks en logique** : les checks que le joueur peut faire tout de suite ;
- 🔒 **bloqué** : aucun check ni l'objectif en logique, il doit attendre des items des autres.

Il faut les yamls et apworlds exacts de la génération, une version d'Archipelago installée avec le bot (celle du
spoiler est choisie), et une partie générée hors race mode. Pour une partie générée sur archipelago.gg à partir des yamls, les options `random` des yamls
ne sont pas reproductibles : la vérification échouera. Si les options sont les bonnes mais qu'un world ne se génère
jamais deux fois pareil, le bot garde les règles de la regénération et prend les items de la room (en se connectant
à chaque slot comme tracker) ; la logique de ce world peut alors être fausse s'il tire aussi ses règles au hasard.
« En logique » ne compte pas les astuces hors logique.

Côté bot, il faut une installation d'Archipelago (`ARCHIPELAGO_PATH` dans le `.env`, avec son Python dans
`.venv/bin/python` ou `ARCHIPELAGO_PYTHON`), ou un dossier qui en contient une par version, chacune avec son
`.venv`. L'image Docker inclut les versions de `ARCHIPELAGO_VERSIONS` (`--build-arg ARCHIPELAGO_VERSIONS="0.6.7 0.6.8"`). **Attention** : la regénération exécute le
code des apworlds envoyés dans le post, sur la machine du bot, dans un processus à part sans les variables
d'environnement du bot (le token Discord n'y est pas), mais avec les mêmes droits sur les fichiers. N'active pas la
logique pour une partie si tu n'as pas confiance dans ses apworlds.

## Pont de chat

Tout message écrit dans le post d'une room est relayé dans le chat de la partie. Si tu as indiqué ton slot
(« Je joue » ou `/claim`), il apparaît à ton nom : le bot ouvre pour ça une connexion de chat avec ton slot, annoncée
dans le jeu, et la ferme après une heure sans message. Sinon, il apparaît au nom du slot du bot, signé
`[Discord] Pseudo:`. Le bot réagit au message s'il a été bloqué (les commandes `!` ne sont jamais relayées) ou
s'il n'a pas pu partir (bot pas connecté à la room). Les messages du chat de la partie apparaissent dans le post. Désactivable
dans **Gérer** sur le panneau.

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
