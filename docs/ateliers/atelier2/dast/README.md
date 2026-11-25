# Exploration dynamique : DAST, fuzzing, IAST

Atelier 2, étape 2. Cible principale : la création de paris (`POST /api/bets`), puis les routes autour (lecture, annulation, résultat, contestation, invitations, mises, dépôts, connexion). Tests faits le 2026-11-25.

## Environnement

- Application lancée en local sur `127.0.0.1:8100` avec `ENV=dev` (OpenAPI servi sur `/openapi.json`), mock PSP sur `8101`, secrets tout neufs générés par `toutbet-admin gen-secrets`, logs JSON de l'appli capturés dans un fichier.
- Deux bases : une SQLite temporaire, puis un PostgreSQL 17 jetable en conteneur (la prod tourne sur PostgreSQL, et c'est là qu'on a trouvé un des défauts).
- Les outils tournent dans Docker (Colima) et joignent l'appli par `host.docker.internal`.
- Les limites de débit sont relevées pendant les scans, sinon ZAP et le fuzzing ne voient que des 429. On a vérifié les vraies limites à part (test D5).
- `TRUSTED_PROXY_IPS` pointe sur une adresse inutilisée, pour qu'aucun client de test ne soit pris pour le proxy.
- Versions : ZAP 2.17.0 (`zaproxy/zap-stable`), ffuf 2.1.0-dev compilé dans un conteneur `golang:1.25-alpine`, schemathesis 4.34.2 (`uvx`), ddtrace 4.17.2 avec `ghcr.io/datadog/dd-apm-test-agent`.

Deux utilisateurs de test (alice, bookie, et bob) servent aux scans authentifiés. Un jeton ne vit que 15 minutes, donc chaque scan authentifié repart d'une connexion fraîche et dure moins de 12 minutes.

## Tests effectués

D = DAST, F = fuzzing, I = IAST. Les fichiers cités sont dans ce dossier.

| # | Type | Outil | Cible | Idée / charge | Résultat |
|---|---|---|---|---|---|
| D1 | DAST | curl | toute la surface HTTP | 20 requêtes à la main : 404, 405, 401, 422, `Host` piégé, préflight CORS d'une origine inconnue, JSON tronqué, JSON imbriqué sur 30 000 niveaux, corps de 70 Ko, `Content-Length: abc`, traversée `../.env` et `..%2f` | Aucune trace ni version dans les réponses. Deux fuites mineures relevées plus bas. `resultats/surface.txt` |
| D2 | DAST | ZAP baseline | front statique, sans compte | scan passif + spider | 0 échec, 3 alertes : COEP absent (faible, corrigé), en-têtes `Sec-Fetch-*` côté requête et contenu cachable (info). `zap/baseline.*` |
| D3 | DAST | ZAP API scan | les 24 routes de l'OpenAPI, sans compte | scan actif complet | 0 échec, 1 alerte faible : du HTML sur `/` (le front, normal). Toutes les routes protégées répondent 401. `zap/api-unauth.*` |
| D4 | DAST | ZAP API scan | OpenAPI sans `logout` ni `refresh`, avec le jeton d'alice injecté par `ZAP_AUTH_HEADER_VALUE` | scan actif authentifié, 3 041 requêtes | 0 échec, 1 alerte faible (même HTML). 2 039 réponses 422 : la validation stricte arrête les injections. Limite : ZAP n'a envoyé qu'une requête sur le corps de `POST /api/bets`, d'où F4 et F7. `zap/api-auth.*` |
| D5 | DAST | ffuf | `POST /api/auth/login`, `POST /api/bets`, limites réelles | 30 requêtes de suite | login : 5 × 401 puis 429. Création de pari : 20 × 201 puis 429. `ffuf/A2-*.json` |
| D6 | DAST | curl + logs | `X-Forwarded-For: 203.0.113.77` | usurpation d'IP client | Lancé comme dans le README (sans `--no-proxy-headers`), uvicorn croit l'en-tête venant de 127.0.0.1 : l'IP loguée devient 203.0.113.77. Avec `--no-proxy-headers` (comme le Dockerfile), l'en-tête est ignoré. README corrigé |
| D7 | DAST | `scripts/idor_matrix.py` | pari, invitation, dépôt, entre trois comptes | IDOR et contrôle d'accès : tiers, participant, bookie, jeton au `sub` modifié, jeton après déconnexion | 21/21 conformes : 404 pour un tiers, 403 pour un participant qui n'est pas bookie, 401 sur jeton modifié ou révoqué, invitation non rejouable. `resultats/idor.txt` |
| D8 | DAST | `scripts/timing.py` | login et inscription | temps et corps de réponse, email connu ou inconnu, 100 mesures | Corps et statuts identiques. Médianes : login 20,7 ms (connu) contre 20,4 ms (inconnu), inscription 21,4 ms (nouveau) contre 19,5 ms (déjà pris). `resultats/timing-*.txt` |
| D9 | DAST | lecture des logs | logs JSON de l'appli | ce qui sort pendant les scans | Lignes `uvicorn.access` malgré `--no-access-log`, avec le chemin et la query string réels (corrigé). Le reste est propre. `logs/extraits.txt` |
| D10 | DAST | curl | `GET /api/bets?page_size=50` | temps de réponse d'une page pleine | 120 ms pour 50 paris contre 8 ms pour 1 : plusieurs requêtes SQL par pari (N+1). Un seul passage à 3,2 s sous IAST (F9) |
| F1 | Fuzzing | ffuf | `/FUZZ` sans compte | 4 814 chemins (SecLists `common.txt` + 76 chemins à nous : `.env`, `.git/config`, `toutbet.db`, `admin`, `debug`, `metrics`...) | 13 réponses non 404 : fichiers du front, `/docs`, `/redoc`, `/openapi.json` (dev), `/healthz`. Rien de sensible. `ffuf/B4-paths-root.json` |
| F2 | Fuzzing | ffuf | `/FUZZ` avec jeton | même liste | Même résultat. `ffuf/B4-paths-root-auth.json` |
| F3 | Fuzzing | ffuf | `/api/FUZZ` avec jeton | 4 751 mots | Seul `/api/me` répond. `ffuf/B4-paths-api-auth.json` |
| F4 | Fuzzing | ffuf | corps de `POST /api/bets`, SQLite | 236 charges sur chaque champ : XSS, SQL, gabarits, NUL, surrogates, RTL, espaces, bornes entières, flottants, `NaN`, dates extrêmes, champs en trop (`status`, `bookie_id`, `platform_commission_bps`...) | **12 erreurs 500** : une date en 9999 avec décalage négatif ou en 0001 avec décalage positif déborde en UTC (corrigé). Bornes, types et champs en trop bien refusés (422). `ffuf/B5-*.json` |
| F5 | Fuzzing | ffuf | même campagne + emails de connexion, PostgreSQL, avant correction NUL | 245 charges | **6 erreurs 500** : un caractère NUL dans le titre, la description ou l'email de connexion fait planter PostgreSQL (corrigé). La connexion est publique, donc un anonyme pouvait déclencher une 500. `ffuf/B5-pg-avant-*.json` |
| F6 | Fuzzing | ffuf | même campagne, PostgreSQL, après corrections | 245 charges | 0 erreur 500. `ffuf/B5-pg-apres-*.json` |
| F7 | Fuzzing | schemathesis | 13 opérations `/api/bets*`, invitations, wallet, dépôts, `/api/me`, SQLite | génération par propriétés depuis l'OpenAPI, NUL autorisé, phases coverage, fuzzing et stateful | 43 539 cas, 0 erreur serveur, aucune réponse > 2 s. 46 écarts de conformité, voir plus bas. `schemathesis/sqlite-*` |
| F8 | Fuzzing | schemathesis | `POST` dont login, PostgreSQL, avant correction NUL | 24 680 cas | 1 erreur serveur trouvée seule : NUL dans l'email de `POST /api/auth/login`. Ce run a appelé `logout` avec le jeton d'alice, la suite a reçu des 401 : erreur de filtre de notre part. `schemathesis/pg-avant-*` |
| F9 | Fuzzing | schemathesis | 14 opérations, PostgreSQL, après corrections | 12 008 cas | 0 erreur serveur. 51 écarts de conformité, plus un `GET /api/bets` à 3,2 s sous IAST (voir D10). `schemathesis/pg-apres-*` |
| F10 | Fuzzing | ffuf | `/FUZZ` | erreur de manip : deux listes en mode clusterbomb, environ 300 000 requêtes en 5 minutes avant arrêt | Aucune 5xx, l'appli a tenu. Le run n'est pas gardé |
| I1 | IAST | ddtrace | appli témoin volontairement vulnérable | SQL concaténé et MD5 sur un paramètre de requête | `SQL_INJECTION` et `WEAK_HASH` remontés avec la ligne exacte : la chaîne IAST marche bien. `iast/canary*` |
| I2 | IAST | ddtrace | ToutBet' sur SQLite pendant D2 à D4, F1 à F4, F7, D7 et D8 | suivi de teinte des entrées HTTP jusqu'aux puits (SQL, hash, eval...) | 64 401 requêtes instrumentées, 594 322 passages dans un puits SQL contrôlés, **0 vulnérabilité**. `iast/sqlite.txt` |
| I3 | IAST | ddtrace | ToutBet' sur PostgreSQL pendant F5, F6, F8, F9 | idem | 37 236 requêtes, 191 298 puits SQL contrôlés, **0 vulnérabilité**. `iast/postgresql.txt` |
| I4 | IAST | logs + `X-Request-ID` | les 22 erreurs 500 | corrélation réponse, événement `http.request` et `error.unhandled` par `request_id` | 22 traces retrouvées, avec la ligne de code fautive. Les paramètres SQL restent masqués (`hide_parameters`). `logs/` |

L'IAST lancé est un vrai IAST open source : ddtrace avec `DD_IAST_ENABLED=true`, l'appli lancée par `uv run --with ddtrace ddtrace-run uvicorn ...`, les traces envoyées à l'agent de test Datadog en local et lues par `scripts/iast_report.py` (le rapport est dans `meta_struct.iast` de chaque span). Il marche en Python 3.13 sans compte Datadog. On a d'abord vérifié qu'il détecte bien quelque chose sur une appli témoin (I1), sinon « 0 vulnérabilité » ne voudrait rien dire. Il ne voit pas les deux plantages (F4, F5) : ce ne sont pas des injections, c'est le fuzzing qui les trouve.

## Logs serveur et stacktraces

Avant correction, 22 erreurs 500 au total. La réponse est toujours générique :

```json
{"error":{"code":"INTERNAL_ERROR","message":"Erreur interne.","request_id":"03fff4436214417eb4dbadafacca3fdb"}}
```

La trace n'existe que côté serveur, dans l'événement `error.unhandled`. Débordement de date (SQLite, 12 fois) :

```text
File "src/toutbet/api/bets.py", line 34, in create
    bet = create_bet(db, user=user, data=body, now=clock.now())
File "src/toutbet/services/bets.py", line 60, in create_bet
    closes_at = data.closes_at.astimezone(UTC)
OverflowError: date value out of range
```

Caractère NUL (PostgreSQL, 10 fois) : 5 sur la création de pari et 5 sur la connexion. Ffuf en déclenche 6 (F5, titre, description et email de connexion), schemathesis 3 (F8 : un seul cas, rejoué pour le réduire), et la dixième est une requête isolée sur `POST /api/bets` à 13:57:52, juste avant la campagne ffuf.

Création de pari :

```text
File "src/toutbet/services/bets.py", line 83, in create_bet
sqlalchemy.exc.DataError: (psycopg.DataError) PostgreSQL text fields cannot contain NUL (0x00) bytes
[SQL: INSERT INTO bets (id, bookie_id, title, description, ...) VALUES (...)]
[SQL parameters hidden due to hide_parameters=True]
```

Connexion :

```text
File "src/toutbet/services/accounts.py", line 96, in authenticate
(Background on this error at: https://sqlalche.me/e/21/9h9h)
```

Le code `9h9h` est celui de `DataError`, la même erreur que pour les paris. Seule la trace du pari est gardée en entier.

Traces complètes dans `logs/stacktrace-overflow-sqlite.txt` et `logs/stacktrace-nul-postgresql.txt`, liste des 22 erreurs dans `logs/erreurs-*-avant.jsonl`. On a vérifié qu'aucune valeur saisie (titre, email) n'apparaît dans ces traces.

## Informations sur la structure interne

| Où | Ce qui fuit | Verdict |
|---|---|---|
| `/docs`, `/redoc`, `/openapi.json` | toute l'API, schémas compris | Seulement avec `ENV=dev`, 404 ailleurs (test `test_as16_docs_disabled_outside_dev`). Accepté |
| Erreurs 422 | messages Pydantic en anglais (`Input should be...`, `Field required`), `loc` en `body`/`query`/`path`/`header`, regex des champs : on reconnaît FastAPI et Pydantic | Pas de version, pas de valeur saisie. Le front s'en sert pour afficher les erreurs. Accepté |
| Erreur 422 sur un UUID | `found 'z' at 0` : un caractère saisi renvoyé | Contraire à T-I02 (« sans les valeurs reçues »). Corrigé |
| Erreur 422 sur un champ en trop | le nom du champ inconnu (`zz<b>`) renvoyé dans `loc` | Nom de champ, pas une valeur, en JSON avec `nosniff`. Accepté |
| `Host` non autorisé, préflight CORS refusé | texte brut `Invalid host header`, `Disallowed CORS origin` : Starlette | Déjà noté dans le README (écarts). Accepté |
| HTTP mal formé (`Content-Length: abc`) | `Invalid HTTP request received.` : uvicorn | Répond avant l'appli. En prod, Caddy est devant. Accepté |
| Méthode non prévue sur `/api/...` | `405` avec `Allow: GET, HEAD`, et `GET` sur une route POST donne 404 : les requêtes retombent sur le montage statique `/` | Révèle le montage statique, rien de plus. Accepté |
| Fichiers statiques | `ETag` et `Last-Modified` (date des fichiers) | Normal pour du cache. Accepté |
| Toutes les réponses | `X-Request-ID` aléatoire, pas d'en-tête `Server` | Voulu (support). OK |
| Clé publique des reçus | base64 signalé par ZAP | Publique par conception. OK |
| Temps de réponse login et inscription | écarts de 0,3 ms et 1,9 ms en médiane | Dans le bruit (p90 qui se chevauchent), corps identiques, et 5 essais par minute au plus. Accepté |

## Corrections

Chaque correction a son test de régression, vérifié en échec sans le correctif.

| Commit | Défaut | Test |
|---|---|---|
| `fix: reject bet dates that overflow once converted to utc` | 500 sur une date en 9999 ou 0001 avec décalage horaire | `test_as05_dates_outside_utc_range_rejected_without_server_error` (6 cas) |
| `fix: stop echoing input characters in uuid validation errors` | le message 422 recopiait un caractère saisi | `test_as16_invalid_uuid_error_does_not_echo_characters` |
| `fix: keep the uvicorn access log off once json logging is configured` | `configure_logging()` réactivait le log d'accès coupé par `--no-access-log` | `test_configure_logging_keeps_uvicorn_access_log_off` |
| `fix: send cross-origin-embedder-policy on every response` | alerte ZAP 90004 | assertion ajoutée dans `test_security_headers_on_api_and_static` |
| `fix: reject nul characters in request strings before they reach postgresql` | 500 sur PostgreSQL, y compris sur la connexion anonyme | `test_as16_nul_character_in_bet_rejected_before_the_database`, `test_as16_nul_character_in_login_rejected_before_the_database` |
| `docs: start uvicorn without proxy headers in the local run instructions` | commande du README différente du Dockerfile (D6) | pas de test, c'est de la doc |

Après corrections, la même campagne (F6, F9) ne donne plus aucune 500, les 422 ne recopient plus rien et le log ne contient plus de ligne `uvicorn.access`.

## Accepté, avec justification

- Codes de retour absents de l'OpenAPI (schemathesis ST103 : 401, 403, 404, 409, 429...). C'est de la doc de dev, pas un défaut de sécurité.
- Requête valide selon le schéma mais refusée (ST202) : règles métier impossibles à écrire en OpenAPI, comme une échéance au moins 5 minutes dans le futur.
- `Allow` incohérent et 404 au lieu de 405 (ST204, ST205) : voir le montage statique plus haut.
- Titre fait d'espaces, de caractères invisibles (U+200B) ou avec un U+202E qui inverse l'affichage : accepté par l'API. Le front l'affiche avec `textContent`, donc pas de XSS. Ça peut tromper un invité sur le titre d'un pari, risque faible, à reprendre si on durcit les textes.
- Liste des paris en N+1 (D10) : 120 ms la page de 50, ça tient. À surveiller dans le test de charge Locust de l'étape 3.
- Le premier relevé de temps (`resultats/timing-sqlite-iast.txt`) compte 30 corps distincts parce que le `request_id` change à chaque réponse. Le script a été corrigé pour l'ignorer, le second relevé (`timing-postgresql.txt`) est celui qui compte. Le compte connu se verrouille au 10e échec, les mesures suivantes passent donc par la branche « verrouillé », qui coûte le même hash.

## Rejouer

```sh
docker run --rm -v <volume>:/zap/wrk zaproxy/zap-stable zap-baseline.py -t http://host.docker.internal:8100/ -r baseline.html -J baseline.json -I -a
docker run --rm -e ZAP_AUTH_HEADER_VALUE="Bearer <jeton>" -v <volume>:/zap/wrk zaproxy/zap-stable \
  zap-api-scan.py -t /zap/wrk/openapi-auth.json -f openapi -O http://host.docker.internal:8100 -r api-auth.html -J api-auth.json -I -a
ffuf -w strings.txt -u http://host.docker.internal:8100/api/bets -X POST -H "Authorization: Bearer <jeton>" \
  -H 'Content-Type: application/json' -d '{"title":"FUZZ","event_starts_at":"...","closes_at":"...","bookie_commission_bps":100}' -mc all
uvx schemathesis run http://127.0.0.1:8100/openapi.json -H "Authorization: Bearer <jeton>" \
  --include-path-regex '^/api/(bets|invitations|me|wallet|deposits)' --checks all --max-response-time 2 --generation-allow-x00 true
DD_IAST_ENABLED=true DD_TRACE_AGENT_URL=http://127.0.0.1:8126 uv run --with ddtrace ddtrace-run \
  uvicorn --factory toutbet.main:build_app --port 8100 --no-server-header --no-access-log --no-proxy-headers
uv run python docs/ateliers/atelier2/dast/scripts/iast_report.py
uv run python docs/ateliers/atelier2/dast/scripts/idor_matrix.py http://127.0.0.1:8100
uv run python docs/ateliers/atelier2/dast/scripts/timing.py http://127.0.0.1:8100 100
```

Aucun jeton ni secret dans ce dossier : l'en-tête `Authorization` est remplacé par `<redacted>` dans les sorties ffuf, schemathesis le masque de lui-même (`[Filtered]`), et le gros journal ndjson de schemathesis, qui contenait les jetons, n'est pas versionné.
