# Atelier 4, étape 3 : plan de chaos engineering

Version du 2026-11-25. On choisit trois dépendances dont ToutBet' ne peut pas se passer, on dit ce qui doit se passer quand elles cassent, et on vérifie une des hypothèses pour de vrai sur une pile locale.

## 1. Trois services à perturber

### 1.1 Le PSP : 10 s de latence, puis panne

Le client PSP coupe à 2 s pour la connexion et 5 s pour la lecture (`src/toutbet/psp/client.py`). Avec 10 s de latence, chaque appel dure donc 5 s puis échoue, il n'attend jamais 10 s.

| Opération | Impact attendu | Échec fermé ? |
|---|---|---|
| Dépôt (`POST /api/deposits`) | dépôt `failed`, réponse 503 `PSP_UNAVAILABLE` au bout de 5 s, clé d'idempotence libérée, rien n'est crédité | oui : le crédit ne vient que d'un webhook signé |
| Retrait (`POST /api/withdrawals`) | le PSP a peut-être payé : retrait laissé `pending`, solde débité, 503 `WITHDRAWAL_PENDING`, clé gardée (un rejeu reçoit 409). Le worker le réconcilie 5 min plus tard par sa référence | oui : on ne rembourse jamais tant que le PSP n'a pas dit non, sinon on retombe sur le double paiement N1 de la revue R1 |
| KYC (`POST /api/kyc/start`) | 503, statut KYC inchangé | oui |
| Webhooks entrants | s'ils arrivent en retard, le dépôt reste `pending` plus longtemps | oui |
| Reste de l'application | ne parle pas au PSP, doit rester normal | à vérifier : routes synchrones et pool de 40 threads partagé |

Point d'attention : les routes synchrones de FastAPI passent toutes par un pool de 40 threads. 40 appels PSP lents en même temps occupent tout le pool, et même `/healthz` attend. La revue R1 l'avait signalé (« jusqu'à 7 s »), l'atelier 2 ne l'avait pas mesuré. C'est ce qu'on mesure en section 4.

Panne franche (PSP arrêté) : la connexion est refusée en quelques millisecondes, donc pas de saturation. Les retraits restent `pending`. Au retour, le PSP ne connaît pas le virement (404) et le worker le renvoie sous la même clé d'idempotence, ce qui ne peut pas créer un second virement.

Rayon d'impact, par paliers :

1. local, PSP simulé, un seul utilisateur ;
2. local, 60 appels en même temps pour saturer le pool (c'est l'expérience faite ici) ;
3. staging, PSP de test derrière toxiproxy, latence sur 5 % des appels ;
4. production : pas avant d'avoir corrigé la saturation du pool, et seulement pendant un Game Day annoncé, interrupteurs prêts.

### 1.2 PostgreSQL : arrêt, lenteur, pool épuisé

| Perturbation | Impact attendu | Échec fermé ? |
|---|---|---|
| Base arrêtée (`docker stop`, connexion refusée) | toutes les routes qui lisent la base répondent vite en 500 au corps générique, `error.unhandled` dans les logs, le worker écrit `worker.tick_failed`. `/healthz` reste à 200 car il ne teste pas la base : un répartiteur de charge continuerait d'envoyer du trafic | oui : l'authentification relit la session en base, sans base personne n'est authentifié. Aucune transaction à moitié faite : débit, écriture du grand livre et clé d'idempotence sont dans la même transaction |
| Base figée (`docker pause`, réseau qui ne répond plus) | aucune limite de temps n'est réglée côté client (ni `connect_timeout`, ni `statement_timeout`) : chaque requête attend sans fin, les 40 threads se remplissent et toute l'API gèle, `/healthz` compris. Même mécanisme que le PSP lent de la section 4, mais sans coupure à 5 s. À régler avant tout essai en staging | oui pour l'argent, non pour la disponibilité |
| Coupure entre l'appel PSP et `mark_sent` d'un retrait | le virement est fait mais le retrait reste `pending` : le worker le retrouve chez le PSP et le passe `sent` | oui, pas de second virement |
| Lenteur (500 ms par requête) | le verrou de tête du grand livre est tenu plus longtemps : les mises passent une par une encore plus lentement (l'atelier 2 plafonnait déjà vers 100 mises/s) | oui, mais la latence explose |
| Pool épuisé | SQLAlchemy garde 5 connexions par processus et en ouvre 10 de plus. Au-delà, une requête attend 30 s puis échoue en 500. Le pool de threads (40) est plus grand que le pool de connexions (15), donc sous charge 25 threads attendent une connexion | oui |

Rayon d'impact : local avec `docker stop` puis `docker pause` 30 s, puis `pg_terminate_backend` sur les connexions de l'application, puis bascule sur réplique en staging, jamais en production hors Game Day.

### 1.3 Le worker : arrêté

| Tâche du worker | Impact attendu | Échec fermé ? |
|---|---|---|
| Clôture des paris à `closes_at` | statut pas mis à jour, mais l'API refuse déjà une mise après `closes_at` sans attendre le worker (`test_as07_stake_after_close_rejected_before_worker_runs`) | oui |
| Règlement après la fenêtre de contestation | l'argent reste au séquestre, personne n'est payé | oui : rien ne sort sans le worker, c'est juste en retard |
| Escalade des paris sans résultat après 72 h | retardée | oui |
| Réconciliation des retraits `pending` | les retraits restent `pending`, le montant reste réservé | oui |
| `ledger.head` toutes les 5 min | plus de hash publié : pendant la coupure, une réécriture du grand livre serait moins facile à dater | non, c'est la seule perte de sécurité. Alertes `WORKER_ARRETE` et `TETE_GRAND_LIVRE_ABSENTE` |
| Purge des clés d'idempotence | la table grossit | sans effet sur la sécurité |

Au redémarrage, deux workers peuvent tourner en même temps quelques secondes. Le règlement est sûr en parallèle (`test_as11_concurrent_settlement_pays_once`) et la réconciliation utilise des `UPDATE` conditionnels et la même clé d'idempotence chez le PSP.

Rayon d'impact : local 10 min, puis staging pendant une fenêtre de contestation ouverte, puis Game Day.

## 2. Création de paris en panne : garder l'application utilisable

Si la création de paris casse (bug, base lente sur la table `bets`, abus), on la coupe seule et tout le reste continue : connexion, portefeuille, dépôts et retraits, lecture des paris, mises sur les paris existants, invitations, contestations, modération et worker.

Ce qui existe et ce qu'on a ajouté :

- l'interrupteur `BET_CREATION_ENABLED` existait déjà côté API (`src/toutbet/config.py`, `src/toutbet/api/deps.py`), comme `STAKES_ENABLED`, `DEPOSITS_ENABLED` et `WITHDRAWALS_ENABLED`. Désactivé, `POST /api/bets` répond 503 `FEATURE_DISABLED` ;
- on a remplacé le message générique par un message clair en français : « La création de paris est momentanément indisponible. Vos paris, vos mises et votre portefeuille restent accessibles. » (un message par interrupteur) ;
- `GET /api/me` renvoie `bet_creation_enabled`. Le front affiche alors un bandeau en haut de chaque page, retire le lien « Créer un pari » et remplace le formulaire par une explication ;
- tests : `tests/security/test_feature_switches.py::test_bet_creation_switch_keeps_the_rest_usable` (503 et message, `/api/me`, liste des paris, mise, dépôt toujours possibles) et `tests/e2e/test_e2e_bets.py::test_e2e_bet_creation_switch_shows_a_banner_and_keeps_the_rest` (navigateur sur une pile lancée avec `BET_CREATION_ENABLED=false` : bandeau visible, pas de lien de création, formulaire remplacé, 503 si on force l'appel, portefeuille affiché, et bandeau absent sur une pile normale).

Qui le bascule : l'ops ou le Lead d'astreinte (matrice de `iam.md`). Comment : passer la variable à `false` dans la configuration de l'environnement et redéployer, ce qui prend le temps d'un redémarrage. Limite : la configuration est lue au démarrage, il n'y a pas de bascule à chaud. Une table d'interrupteurs relue toutes les 30 s serait plus rapide, on ne l'a pas faite.

## 3. Conditions d'arrêt immédiat

Pendant toute expérience, une sonde tourne en parallèle et `toutbet-admin verify-ledger` est lancé chaque minute. On arrête tout de suite si :

| Condition | Seuil | Mesure |
|---|---|---|
| Grand livre incohérent | 1 échec de `verify-ledger`, ou 1 `ledger.verify_failed` | tâche toutes les minutes |
| Argent déplacé deux fois | 1 référence payée deux fois chez le PSP, 1 clé d'idempotence débitée deux fois, 1 dépôt crédité deux fois | comptage des virements du PSP par référence, écritures `withdrawal` par clé |
| Transaction perdue | 1 opération d'argent acceptée (201) absente du grand livre, ou 1 retrait dans un état autre que `pending`, `sent` ou `failed`. Pour l'argent, le pourcentage toléré est 0 | requête SQL de contrôle |
| Erreurs 500 | plus de 1 % de réponses 500 pendant 30 s. Les 503 voulus (`PSP_UNAVAILABLE`, `WITHDRAWAL_PENDING`, `FEATURE_DISABLED`) ne comptent pas, sauf sur une route qui n'appelle pas le service perturbé | `http.request` |
| Authentification ouverte | 1 réponse 2xx avec un jeton invalide, ou 1 webhook mal signé accepté | la sonde envoie un faux jeton et un faux webhook toutes les 10 s |
| Routes non concernées dégradées | p95 de `GET /api/wallet` au-dessus de 2 s pendant 30 s | sonde |

Qui arrête : celui qui mène l'expérience (ops) ou l'observateur (Lead), chacun peut le faire sans demander. Comment, dans l'ordre :

1. retirer la panne : redémarrer le PSP simulé sans `MOCK_PSP_LATENCY_MS`, supprimer le toxique toxiproxy, `docker unpause` la base, relancer le worker ;
2. si l'argent est en jeu : `STAKES_ENABLED=false`, `DEPOSITS_ENABLED=false`, `WITHDRAWALS_ENABLED=false` et redéploiement ;
3. `toutbet-admin verify-ledger`, puis comparaison des virements du PSP avec la table `withdrawals` ;
4. message à l'équipe et note dans le compte rendu.

## 4. Fiche d'expérience : PSP à 10 s sur les dépôts et les retraits

### Hypothèse

Régime permanent, d'après les objectifs de `STEERING.md` : l'API répond (`/healthz` en 200), p95 d'une mise sous 300 ms, moins de 0,1 % de 5xx hors 503 voulus, aucun écart entre grand livre et soldes.

Si le PSP met 10 s à répondre aux dépôts et aux retraits, alors :

- aucun dépôt n'est crédité, chaque dépôt échoue proprement en 503 ;
- chaque retrait reste `pending`, son montant reste réservé, un rejeu avec la même clé est refusé ;
- une fois le PSP revenu, le worker passe chaque retrait à `sent`, et chaque retrait n'est payé qu'une fois ;
- `verify-ledger` reste vert du début à la fin ;
- les routes qui n'appellent pas le PSP (portefeuille, lecture d'un pari, mise, `/healthz`) gardent un p95 sous 300 ms.

### Expérience

- Pile locale : PostgreSQL 17 (même image que la CI, rôle restreint et `grants.sql`) dans un conteneur sur le réseau Docker par défaut, l'API en uvicorn (un processus, comme le `Dockerfile`) sur le port 8300, le PSP simulé sur 8301, le worker. Limites de débit relevées comme pour la charge de l'atelier 2, sinon le seed est bloqué.
- Injection : le PSP simulé a maintenant un réglage de dev `MOCK_PSP_LATENCY_MS` (et `MOCK_PSP_LATENCY_DURATION_S`). La latence est ajoutée après le traitement : le PSP a déjà enregistré le virement quand ToutBet' abandonne, c'est le pire cas pour nous. Au bout de la durée, il redevient rapide sans redémarrer, donc sans perdre sa mémoire des virements (`src/mock_psp/main.py`, test `test_latency_knob_answers_late_after_recording_the_payout`).
- Charge : 12 joueurs approvisionnés et vérifiés, un pari ouvert. Pendant la latence : 12 retraits de 10 € et 48 dépôts de 10 € envoyés en même temps, 6 mises sur le pari existant, un rejeu de retrait avec la même clé, et une sonde qui lit `/healthz`, `/api/wallet` et le pari toutes les 0,5 s.
- Durée : latence de 10 s pendant 8 min, puis retour à la normale, puis attente de la réconciliation (le worker attend qu'un retrait ait 5 min).
- Rayon d'impact : une pile locale jetable, aucune donnée réelle.
- Arrêt : conditions de la section 3.
- Script : `docs/ateliers/atelier4/chaos/psp_latency.py` (`seed`, `steady`, `burst`, `verify`).

### Résultat attendu

| Mesure | Attendu |
|---|---|
| Dépôts pendant la latence | 48 réponses 503 `PSP_UNAVAILABLE` en 5 s environ, 48 dépôts `failed`, 0 crédit |
| Retraits pendant la latence | 12 réponses 503 `WITHDRAWAL_PENDING`, 12 retraits `pending`, soldes débités |
| Rejeu d'un retrait | 409 `IDEMPOTENCY_IN_PROGRESS` |
| Réconciliation pendant la latence | `money.withdrawal_unconfirmed`, retraits toujours `pending` |
| Réconciliation après | 12 retraits `sent`, 12 virements chez le PSP, aucun en double |
| `verify-ledger` | vert à chaque contrôle |
| Routes sans PSP | p95 sous 300 ms |

### Plan de repli

- Pendant l'expérience : conditions et gestes de la section 3.
- Si le PSP réel devient lent en production : couper `DEPOSITS_ENABLED` et `WITHDRAWALS_ENABLED` dès que `PSP_INDISPONIBLE` dure plus de 10 min, pour que les appels lents ne prennent plus les threads de l'API. Les mises sur solde existant, la lecture et la modération continuent.
- Les retraits restés `pending` ne sont jamais remboursés à la main : on attend la réponse du PSP par la réconciliation, ou on vérifie chez le PSP avec la référence (id du retrait).
- Après coup : `verify-ledger`, comparaison des virements du PSP avec la table `withdrawals`, et `toutbet-admin check-alerts` sur les logs de la période.

### Résultat observé

Run du 2026-11-25 entre 15:13 et 15:31 UTC, sur la pile décrite plus haut. Mesures brutes dans `chaos/` : `1-regime-permanent.txt`, `2-latence-rafale.txt`, `2-latence-chronologie.txt`, `2-latence-controle.txt`, `3-panne-rafale.txt`, `3-panne-controle.txt`, `journaux-extraits.txt`.

Déroulé :

| Heure (UTC) | Phase |
|---|---|
| 15:13 | pile démarrée, seed de 12 joueurs (50 € chacun, KYC vérifié, tous dans le même pari) |
| 15:15 | régime permanent : 60 s de sonde, un dépôt et un retrait toutes les 5 s, PSP normal |
| 15:16:26 | PSP simulé relancé avec 10 s de latence pendant 8 min |
| 15:16:37 | rafale : 12 retraits et 48 dépôts en même temps, 6 mises, un rejeu |
| 15:21 à 15:24 | deux tours du worker pendant la latence |
| 15:24:26 | fin de la latence, le PSP redevient rapide sans redémarrer |
| 15:24:47 | tour du worker qui réconcilie les 12 retraits |
| 15:25 | en plus du plan : PSP arrêté, 12 retraits et 12 dépôts, sonde d'authentification ajoutée (faux jeton, faux webhook) |
| 15:26 | PSP relancé, sa mémoire est vide |
| 15:30:48 | le worker renvoie les 12 retraits inconnus du PSP sous la même clé |

Régime permanent (PSP normal) :

| Route | n | p50 | p95 | Statuts |
|---|---|---|---|---|
| `/healthz` | 115 | 1,9 ms | 4,5 ms | 200 |
| `GET /api/wallet` | 115 | 10,0 ms | 18,4 ms | 200 |
| `GET /api/bets/{id}` | 115 | 11,7 ms | 18,7 ms | 200 |
| `POST /api/deposits` | 12 | 26,2 ms | 36,4 ms | 201 |
| `POST /api/withdrawals` | 12 | 41,3 ms | 57,8 ms | 201 |

Les mises ne faisaient pas partie de cette mesure (oubli de notre part), on les compare donc directement à l'objectif de 300 ms.

Pendant la latence :

| Mesure | Attendu | Observé | Verdict |
|---|---|---|---|
| Dépôts | 48 × 503 en 5 s, 0 crédit | 48 × 503 `PSP_UNAVAILABLE`, 48 dépôts `failed`, 0 crédit. Mais 40 réponses en 5,3 s et 20 en 10,2 s | conforme, plus lent que prévu |
| Retraits | 12 × 503, `pending`, soldes débités | 12 × 503 `WITHDRAWAL_PENDING`, 12 `pending`, soldes débités. Le PSP avait déjà enregistré les 12 virements quand on a abandonné | conforme |
| Rejeu d'un retrait | 409 | 409 `IDEMPOTENCY_IN_PROGRESS` | conforme |
| Worker pendant la latence | retraits toujours `pending` | 24 `money.withdrawal_unconfirmed` (12 par tour), retraits toujours `pending`. Chaque tour a duré 60 s au lieu de quelques millisecondes : 12 retraits × 5 s, l'un après l'autre | conforme, avec un effet de bord |
| Après la latence | 12 `sent`, pas de doublon | au premier tour, les 12 retrouvés chez le PSP et passés `sent`, aucun nouveau virement | conforme |
| `verify-ledger` | vert | vert aux 4 contrôles (avant la latence, pendant, après la réconciliation, à la fin). On ne l'a pas lancé chaque minute comme le demande la section 3 | conforme |
| Routes sans PSP | p95 < 300 ms | p95 sur la rafale : `/healthz` 6,3 ms, portefeuille 13,9 ms, mise 15,6 ms. Mais pendant les 5 premières secondes tout a attendu : `/healthz` 4 985 ms, une mise 4 275 ms | **non conforme** |
| Erreurs 500 | 0 | 0 sur tout le run | conforme |

Ce qui s'est passé dans les 5 premières secondes (`2-latence-chronologie.txt`) : les routes synchrones de FastAPI tournent dans un pool de 40 threads. Les 60 appels au PSP en ont pris 40 d'un coup, pour 5 s chacun. Les 20 autres appels PSP, la sonde et les mises ont attendu un thread libre. Dès que les 40 premiers ont abandonné, le reste est reparti normalement (mises à 13 ms). L'hypothèse « les routes sans PSP ne sont pas touchées » est donc fausse : ici le gel n'a duré que 5 s parce que la rafale était unique, mais il suffit de 8 appels PSP par seconde en continu (40 threads / 5 s) pour que l'API reste gelée tant que le PSP est lent. On n'a pas atteint la condition d'arrêt (p95 du portefeuille au-dessus de 2 s pendant 30 s), le gel était trop court.

PSP arrêté, en plus du plan :

- connexion refusée tout de suite : 12 retraits en 503 `WITHDRAWAL_PENDING` et 12 dépôts en 503 `PSP_UNAVAILABLE` en 220 ms environ, aucun gel, `/healthz` à 5 ms ;
- sonde d'authentification : 15 faux jetons refusés en 401, 15 faux webhooks refusés en 400, aucune ouverture ;
- au retour du PSP, sa mémoire était vide : le worker a reçu 404 pour chaque retrait et l'a renvoyé sous la même clé, 12 virements créés une seule fois, 12 retraits `sent`.

Bilan sur tout le run (`3-panne-controle.txt`) : 36 retraits, 36 virements chez le PSP pour 36 références, aucune référence payée deux fois, 36 débits pour 36 retraits, aucun solde négatif, somme des soldes = 600 € déposés − 360 € retirés − 6 € au séquestre = 234 €. `verify-ledger` vert. `toutbet-admin check-alerts` sur le log de l'API lève 5 fois `WEBHOOK_PSP_SIGNATURE_INVALIDE` (les 15 faux webhooks de la sonde, par paquets de 3), rien d'autre, ce qui est le résultat attendu. Après le run, la suite complète passe sur cette même base PostgreSQL avec le rôle restreint : 284 tests.

Ce qu'on en retient :

1. **Les appels PSP ne doivent pas partager le pool de l'API.** Pistes : un limiteur dédié (par exemple 10 appels PSP en même temps au plus, les suivants reçoivent 503 tout de suite), ou un client httpx asynchrone dans des routes asynchrones, et un coupe-circuit qui répond 503 sans appeler le PSP après 5 timeouts en 30 s. En attendant, la réponse est manuelle : couper `DEPOSITS_ENABLED` et `WITHDRAWALS_ENABLED` (plan de repli). Pas fait dans cet atelier.
2. **Le worker fait tout l'un après l'autre.** Un PSP lent bloque aussi la clôture et le règlement des paris : 60 s par tour ici, et ce serait 5 s par retrait en attente. La sécurité tient (l'API refuse les mises après `closes_at` sans le worker), mais il faudrait un budget de temps pour la réconciliation, ou une boucle à part.
3. **L'argent a tenu dans les deux pannes** : le choix de laisser un retrait `pending` au lieu de le rembourser, et la clé d'idempotence égale à l'id du retrait, ont fait exactement ce qu'il fallait, y compris quand le PSP a perdu sa mémoire.
4. **`/healthz` ne dit pas tout.** Il gèle avec le reste (bon signe pour la détection), mais il ne teste pas la base (scénario 1.2). La sonde `HEALTHCHECK` du `Dockerfile` (3 s, 3 essais) ne redémarrerait le conteneur qu'après un gel continu de 90 s.
5. Petit constat sur les logs : la bibliothèque httpx écrit chaque URL appelée au niveau INFO (`journaux-extraits.txt`). Nos URL vers le PSP ne contiennent que des identifiants internes, la clé passe dans un en-tête, mais il vaudrait mieux monter le logger `httpx` à WARNING comme on l'a fait pour `slowapi`.

Une erreur de notre côté pendant le run : le premier essai du régime permanent envoyait des retraits de 1 €, refusés en 422 (minimum 10 €). On a corrigé le script et relancé, ces 12 réponses 422 restent dans le log de l'API.

### Rejouer

```sh
docker run -d --rm --name toutbet-chaos-pg \
  -e POSTGRES_DB=toutbet -e POSTGRES_USER=toutbet_owner -e POSTGRES_PASSWORD=owner-chaos-only \
  -e APP_DB_PASSWORD=app-chaos-only \
  -v "$PWD/deploy/postgres/create-app-role.sh:/docker-entrypoint-initdb.d/10-create-app-role.sh:ro" \
  -p 127.0.0.1:55434:5432 postgres:17
export DATABASE_URL=postgresql+psycopg://toutbet_app:app-chaos-only@127.0.0.1:55434/toutbet
DATABASE_ADMIN_URL=postgresql+psycopg://toutbet_owner:owner-chaos-only@127.0.0.1:55434/toutbet \
  uv run toutbet-admin init-db --grants deploy/postgres/grants.sql
set -a; eval "$(uv run toutbet-admin gen-secrets)"; set +a
export ENV=test PSP_BASE_URL=http://127.0.0.1:8301 PUBLIC_BASE_URL=http://127.0.0.1:8300 \
  ALLOWED_HOSTS=127.0.0.1 TRUSTED_PROXY_IPS=192.0.2.1 \
  MOCK_PSP_PORT=8301 MOCK_PSP_PUBLIC_URL=http://127.0.0.1:8301 \
  MOCK_PSP_WEBHOOK_URL=http://127.0.0.1:8300/api/webhooks/psp
for v in LOGIN REGISTER REFRESH WRITE READ WRITE_PER_IP READ_PER_IP KYC WITHDRAWALS INVITATION_ACCEPT WEBHOOK; do
  export RATE_LIMIT_$v=100000/minute
done
uv run mock-psp > psp-1.log &
uv run uvicorn --factory toutbet.main:build_app --port 8300 --no-server-header --no-access-log --no-proxy-headers > app.log &
uv run toutbet-worker > worker.log &

S=docs/ateliers/atelier4/chaos/psp_latency.py
uv run python $S --state /tmp/chaos.json seed --players 12
uv run python $S --state /tmp/chaos.json steady --seconds 60 --out steady.json
kill %1; MOCK_PSP_LATENCY_MS=10000 MOCK_PSP_LATENCY_DURATION_S=480 uv run mock-psp > psp-2.log &
uv run python $S --state /tmp/chaos.json burst --deposits-per-player 4 --stakers 6 --out burst.json
# attendre la fin de la latence et le tour du worker (environ 9 min)
cat psp-*.log > psp-all.log
uv run python $S --state /tmp/chaos.json verify \
  --database-url postgresql+psycopg://toutbet_owner:owner-chaos-only@127.0.0.1:55434/toutbet --psp-log psp-all.log
uv run toutbet-admin verify-ledger
uv run toutbet-admin check-alerts app.log
kill %2 %3 %4; docker stop toutbet-chaos-pg
```
