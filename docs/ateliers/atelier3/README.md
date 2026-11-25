# Atelier 3 : intégrité des environnements

Le code est validé depuis l'atelier 2. Ici on regarde ce qui peut l'altérer entre le clavier et la production : les postes, l'agent et son contexte, GitHub, les registres publics, la CI et les secrets.

## Fichiers

| Étape | Fichier | Contenu |
|---|---|---|
| 1 | [`supply-chain.md`](supply-chain.md) | schéma annoté du clavier à la production, frontières `TB-SC1` à `TB-SC7`, 18 menaces `T-SC01` à `T-SC18`, points de contrôle, serveurs MCP |
| 1 | [`.github/dependabot.yml`](../../../.github/dependabot.yml) | mises à jour hebdomadaires groupées pour uv, les actions, le `Dockerfile` et le compose |
| 2 | [`sca.md`](sca.md) | vulnérabilités et priorisation, licences et copyleft, SBOM |
| 3 | [`.github/workflows/ci.yml`](../../../.github/workflows/ci.yml) | pipeline d'intégration continue |
| 3 | [`secrets.md`](secrets.md) | inventaire, définition par le pipeline de déploiement, récupération par l'application, impact d'une fuite |
| 3 | [`src/toutbet/config.py`](../../../src/toutbet/config.py), [`tests/unit/test_settings.py`](../../../tests/unit/test_settings.py) | lecture des secrets montés en fichiers dans `/run/secrets` |
| 3 | [`.gitleaksignore`](../../../.gitleaksignore) | trois faux positifs de gitleaks, justifiés à la fin de `secrets.md` |

## Étape 2, SBOM et SCA

`semgrep ci --supply-chain` sur `uv.lock` : 80 dépendances, 0 vulnérabilité, 0 paquet malveillant, aucune version en retard. Aucune licence n'oblige à ouvrir notre code : `psycopg` est en LGPL et importé sans modification, et le seul paquet GPL (`text-unidecode`, en double licence avec l'Artistic License) ne sert qu'aux tests E2E. Détail et méthode de priorisation (atteignabilité, EPSS, KEV) dans [`sca.md`](sca.md).

## Étape 3, pipeline CI

`ci.yml` tourne à chaque push et chaque pull request sur `main`. Six jobs en parallèle, tous avec uv 0.13.6 et `--locked` :

| Job | Ce qu'il fait | Résultat en local |
|---|---|---|
| Lint and types | `uv lock --check`, `ruff check .`, `ruff format --check .`, `mypy src` | OK |
| Unit, abuse and security tests | `uv run --locked pytest` sur SQLite | 267 passés, 1 ignoré (le test PostgreSQL) |
| E2E Playwright | `uv run --locked --group e2e pytest tests/e2e` : Chromium headless installé avec ses bibliothèques système, mis en cache selon la version de Playwright de `uv.lock`. En cas d'échec, traces Playwright, captures d'écran et logs serveur publiés comme artefact (7 jours) | 33 passés, environ 35 s |
| Tests on PostgreSQL | service `postgres:17` figé par digest, rôle `toutbet_app` créé par `deploy/postgres/create-app-role.sh` (le script du compose, lancé avec `docker exec`), suite complète avec `DATABASE_URL` sur le rôle restreint et `DATABASE_ADMIN_URL` sur le propriétaire. Une dernière étape échoue si `test_pg_grants.py` est ignoré au lieu de passer | 268 passés, dont `test_pg_app_role_cannot_update_ledger` |
| Semgrep OSS | conteneur `semgrep/semgrep:1.186.0` figé par digest, `semgrep --test` sur nos règles (3 à l'atelier 3, 4 depuis l'atelier 4), puis `semgrep scan --error` avec les 7 packs de l'atelier 2, `p/github-actions` et `.semgrep/toutbet.yml`, sans jeton | 3/3 tests de règles, 542 règles, 0 alerte |
| Secret scan | gitleaks 8.30.1 (image figée par digest) sur tout l'historique (`fetch-depth: 0`), `--redact` | 0 fuite, après avoir listé 3 faux positifs |

Durcissement du workflow :

- `permissions: contents: read` au niveau du workflow, aucun job ne demande plus ;
- chaque action figée par SHA de commit complet, version en commentaire ;
- `persist-credentials: false` sur chaque checkout, le jeton n'est pas laissé dans `.git/config` ;
- `concurrency` avec `cancel-in-progress`, `timeout-minutes` sur chaque job ;
- déclencheur `pull_request` seulement, pas de `pull_request_target`, aucun secret de dépôt ;
- les images de la CI (PostgreSQL, Semgrep, gitleaks) figées par digest comme celles du compose.

Pour que les traces Playwright soient enregistrées, `tests/e2e/conftest.py` ouvre maintenant ses contextes navigateur avec la fixture `new_context` de pytest-playwright au lieu de `browser.new_context`. Rien d'autre ne change dans les tests.

### Validation locale

Le workflow n'a pas encore tourné sur GitHub (rien n'est poussé). Ce qu'on a vérifié :

- `actionlint` 1.7.12 avec shellcheck : 0 erreur ;
- `uvx zizmor --offline` (1.33.1) : aucune alerte ;
- `uvx check-jsonschema` : `ci.yml` et `dependabot.yml` valides ;
- `act` 0.2.98 avec l'image `catthehacker/ubuntu:act-24.04` : les jobs E2E, lint, tests et Semgrep passent dans un Ubuntu 24.04 neuf. Les E2E ne dépendent donc pas de nos Mac : le job installe Chromium et ses bibliothèques, l'application et le PSP simulé tournent sur les ports 8200 à 8202 avec des secrets générés ;
- le job PostgreSQL n'a pas pu tourner sous act (plus de sous-réseau Docker libre sur notre machine). On a rejoué ses commandes à la main avec la même image, le même `docker exec` du script et les mêmes variables : 268 passés ;
- le job gitleaks monte `$PWD` dans un conteneur, ce qui ne fonctionne pas sous act. Même commande lancée directement sur le dépôt : 0 fuite.

### Réglages GitHub à faire

On ne touche pas aux réglages du dépôt. Ce qui reste à faire à la main :

- protéger `main` : PR obligatoire, les six jobs en checks requis, au moins une revue ;
- activer les alertes et les mises à jour de sécurité Dependabot (les mises à jour de version marchent déjà avec le fichier) ;
- dans Actions, garder l'approbation manuelle des workflows lancés par de nouveaux contributeurs et mettre les permissions par défaut du `GITHUB_TOKEN` en lecture seule.

Aucun secret de dépôt n'est nécessaire.
