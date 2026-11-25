# Atelier 3, étape 3 : stratégie pour les secrets

Version du 2026-11-25. Le sujet demande comment le pipeline de déploiement définit les secrets (à décrire, pas à faire) et comment l'application les récupère. On part de ce qui existe, on décrit la cible, et on a déjà fait la partie lecture côté application.

## 1. Inventaire actuel

Six secrets : les quatre de l'application, générés par `uv run toutbet-admin gen-secrets` (module `secrets` de Python), et les deux mots de passe PostgreSQL du compose, générés avec `openssl rand -hex 32`.

| Secret | Sert à | Contrôle au démarrage |
|---|---|---|
| `JWT_SECRET` | signer et vérifier les jetons d'accès HS256 | 32 octets minimum |
| `RECEIPT_SIGNING_KEY` | graine Ed25519 qui signe les reçus de mise | base64url de 32 octets exactement |
| `PSP_WEBHOOK_SECRET` | vérifier le HMAC des webhooks du PSP | 32 octets minimum |
| `PSP_API_KEY` | s'authentifier auprès du PSP (paiements, KYC, virements) | 32 octets minimum |
| `APP_DB_PASSWORD` | rôle `toutbet_app` : `SELECT, INSERT, UPDATE`, pas de DDL, grand livre en ajout seul | passe dans `DATABASE_URL` |
| `POSTGRES_OWNER_PASSWORD` | rôle `toutbet_owner`, propriétaire du schéma | passe dans `DATABASE_ADMIN_URL` |

Qui a besoin de quoi (d'après `docker-compose.yml`) :

| Secret | API | worker | `init-db` | `postgres` | PSP simulé |
|---|---|---|---|---|---|
| `JWT_SECRET` | oui | | | | |
| `RECEIPT_SIGNING_KEY` | oui | | | | |
| `PSP_WEBHOOK_SECRET` | oui (vérifie) | | | | oui (signe) |
| `PSP_API_KEY` | oui | oui (réconciliation des retraits) | | | oui (vérifie) |
| `APP_DB_PASSWORD` | oui | oui | oui | oui (crée le rôle) | |
| `POSTGRES_OWNER_PASSWORD` | | | oui | oui | |

Le worker ne reçoit que `PSP_API_KEY` et son accès à la base : il n'a pas les clés JWT, de reçus ni de webhook. Le rôle propriétaire ne sert qu'à `init-db`, jamais à l'API ni au worker. `toutbet-admin` (`verify-ledger`, `create-moderator`) tourne dans le conteneur de l'API avec `DATABASE_URL`.

Comment ils arrivent aujourd'hui : `.env` (ignoré par Git et par le contexte Docker) est lu par Compose, qui passe des variables d'environnement aux conteneurs. L'application les lit avec pydantic-settings : refus de démarrer si un secret manque ou est trop court, avec le nom de la variable mais jamais sa valeur (`hide_input_in_errors`), type `SecretStr` qui masque la valeur dans les `repr` et les logs, URLs du PSP et publique en https obligatoire quand `ENV=prod`.

Limites :

- une variable d'environnement se lit avec `docker inspect`, dans `/proc/<pid>/environ` et passe aux processus enfants ;
- `.env` est en clair sur le disque du poste ;
- aucune rotation prévue, et rien n'empêche de copier les mêmes valeurs d'un environnement à l'autre.

La CI n'utilise aucun secret de dépôt : les tests E2E génèrent leurs quatre secrets à chaque lancement, le job PostgreSQL utilise des mots de passe jetables écrits dans `ci.yml`, valables le temps du job sur un runner détruit ensuite.

## 2. Définition par le pipeline de déploiement (cible, non réalisée)

### Le coffre, seule source

Les secrets vivent dans un coffre : HashiCorp Vault, ou le gestionnaire du cloud retenu (AWS Secrets Manager, GCP Secret Manager, Azure Key Vault). Un chemin par environnement et par service, et une politique par service :

```text
kv/toutbet/prod/api       JWT_SECRET, RECEIPT_SIGNING_KEY, PSP_WEBHOOK_SECRET, PSP_API_KEY
kv/toutbet/prod/worker    PSP_API_KEY
database/creds/toutbet-app      identifiants PostgreSQL dynamiques (rôle applicatif)
database/creds/toutbet-migrate  identifiants courts pour init-db (rôle propriétaire)
kv/toutbet/staging/...    mêmes chemins, autres valeurs
```

Le worker n'a le droit de lire que `kv/toutbet/<env>/worker` et `database/creds/toutbet-app`. Les valeurs sont générées dans le coffre ou par un administrateur, jamais recopiées depuis un poste.

### Ce que fait le pipeline

Le pipeline de déploiement ne manipule pas les valeurs. Il déploie un manifeste qui dit quel secret monter et où (chemin dans le coffre, version), et c'est la plateforme d'exécution qui va les chercher avec sa propre identité. Les valeurs ne passent donc jamais par GitHub, ni par les logs du pipeline.

- **GitHub Environments.** Deux environnements, `staging` et `production`. `production` exige l'approbation d'un relecteur qui n'est pas l'auteur du changement (séparation codeur et déployeur, vue en cours) et n'accepte que la branche `main`.
- **OIDC, pas de clé longue durée.** Le job de déploiement demande un jeton OIDC à GitHub (`permissions: id-token: write`). Ce jeton dure quelques minutes et porte `sub: repo:AirKyzzZ/toutbet:environment:production`. Le cloud ou Vault ne fait confiance qu'à ce `sub` exact et donne en échange un accès court, limité au déploiement. Aucune clé cloud n'est stockée dans les secrets GitHub.
- **Un jeu de secrets par environnement.** Valeurs différentes en dev, staging et prod. Le dev garde `.env` et `gen-secrets`, la prod n'a jamais de `.env`.
- **Rien dans les images.** `.env` est exclu du contexte Docker, le `Dockerfile` n'a aucun `ARG` ni `ENV` secret, l'image est la même pour tous les environnements. Si un build avait besoin d'un secret, ce serait `RUN --mount=type=secret`, qui ne laisse rien dans les couches. gitleaks tourne déjà sur l'historique ; on ajouterait un scan de secrets sur l'image construite.

Esquisse du job de déploiement, à ne pas prendre pour un fichier testé :

```yaml
deploy:
  needs: [lint, tests, e2e, postgres, semgrep, gitleaks]
  if: github.ref == 'refs/heads/main'
  environment: production
  permissions:
    contents: read
    id-token: write
  steps:
    - uses: <action d'authentification OIDC du cloud>@<sha complet>
      with:
        role: deploy-toutbet-prod
    - run: deploy --image registry.example/toutbet@${IMAGE_DIGEST}
```

### Rotation

| Secret | Comment on le change |
|---|---|
| `JWT_SECRET` | Ajouter un `kid` dans l'en-tête du jeton. L'API signe avec la clé courante et accepte la courante et la précédente pendant le chevauchement, au moins 15 min (durée d'un jeton d'accès), puis retire l'ancienne. En urgence, changer la clé sans chevauchement : tous les jetons tombent et tout le monde se reconnecte, ce que le design accepte déjà. |
| `RECEIPT_SIGNING_KEY` | Les reçus portent déjà un `key_id` (empreinte de la clé publique). Nouvelle clé pour les nouveaux reçus, et on garde la liste des anciennes clés publiques pour vérifier les anciens reçus. Jamais les anciennes clés privées. |
| `PSP_API_KEY` | Les PSP permettent deux clés actives : créer la nouvelle, déployer, révoquer l'ancienne. |
| `PSP_WEBHOOK_SECRET` | Rotation côté PSP avec une période où il signe avec les deux secrets ; l'API accepte les deux pendant cette période. |
| `APP_DB_PASSWORD` | Remplacé par des identifiants dynamiques (moteur `database` de Vault) : chaque instance reçoit un utilisateur PostgreSQL temporaire, membre de `toutbet_app` donc avec ses seuls droits, valable une heure et renouvelé, supprimé à l'expiration. Plus de mot de passe partagé à faire tourner. |
| `POSTGRES_OWNER_PASSWORD` | Jamais utilisé en fonctionnement. Rôle Vault dédié aux migrations avec une durée de vie de quelques minutes, ou rôle statique avec rotation automatique. |

## 3. Récupération par l'application

### Aujourd'hui, et ce qu'on a ajouté

L'application lit toujours sa configuration avec pydantic-settings, mais elle sait maintenant aussi lire des fichiers montés dans `/run/secrets` (commit `feat: read secrets from files mounted in /run/secrets before the environment`, dans `src/toutbet/config.py`). C'est l'option `secrets_dir` de pydantic-settings, donc peu de code :

- un fichier par variable, nommé comme elle sans tenir compte de la casse (`/run/secrets/jwt_secret` ou `/run/secrets/JWT_SECRET`), le saut de ligne final est retiré ;
- ordre de priorité : arguments explicites (les tests), puis **fichier dans `/run/secrets`**, puis variable d'environnement, puis `.env` ;
- mêmes contrôles qu'avant : un secret trop court lu dans un fichier est refusé même si la variable d'environnement contient une bonne valeur, et l'erreur donne le nom sans la valeur ;
- si `/run/secrets` n'existe pas (poste de dev, tests), il est ignoré sans avertissement ;
- valable pour l'API (`load_settings`), le worker (`load_worker_settings`) et `toutbet-admin` (`load_database_settings`, donc aussi `DATABASE_URL` et `DATABASE_ADMIN_URL` en fichiers). Pas pour le PSP simulé, qui ne sert qu'en dev.

Pourquoi le fichier gagne : en production il vient du coffre. Une variable du même nom ne peut être qu'un reste (vieux compose, `.env` recopié) et ne doit pas masquer en silence un secret qu'on vient de changer. Ça évite aussi qu'un `.env` copié depuis `.env.example`, où les secrets sont vides, cache le fichier.

Tests dans `tests/unit/test_settings.py` :

- `test_mounted_secret_files_win_over_environment`
- `test_worker_reads_its_secrets_from_mounted_files`
- `test_weak_mounted_secret_refused_without_printing_it`
- `test_missing_secrets_directory_is_skipped_silently`

Les trois premiers échouent sur l'ancien `config.py`, on l'a vérifié avant de committer.

### Pourquoi des fichiers plutôt que des variables

Un fichier dans `/run/secrets` n'apparaît pas dans `docker inspect` ni dans `/proc/<pid>/environ`, n'est pas transmis aux processus enfants, peut être en mémoire seulement (tmpfs), lisible par le seul utilisateur 10001 de l'image, et peut être réécrit par un agent du coffre lors d'une rotation.

### Comment les fichiers arrivent

- **Docker Compose.** Un bloc `secrets:` avec `file:` monte chaque fichier en `/run/secrets/<nom>`. Sans Swarm, c'est un simple montage : les options `uid`, `gid` et `mode` sont ignorées, le fichier de l'hôte doit donc être lisible par l'utilisateur 10001. On n'a pas basculé notre compose pour garder le démarrage local simple avec `.env`, mais l'application est prête :

  ```yaml
  services:
    app:
      secrets: [jwt_secret, receipt_signing_key, psp_webhook_secret, psp_api_key, database_url]
    worker:
      secrets: [psp_api_key, database_url]
  secrets:
    jwt_secret:
      file: ./secrets/jwt_secret
  ```

- **Kubernetes.** Un `Secret` monté en volume sur `/run/secrets` (tmpfs), alimenté depuis le coffre par External Secrets Operator ou le driver CSI Secrets Store.
- **Agent Vault en sidecar.** Il s'authentifie avec l'identité du pod, rend les modèles dans un volume en mémoire partagé sur `/run/secrets` et renouvelle les identifiants PostgreSQL dynamiques. Pour la base il écrit directement `database_url` :

  ```text
  {{ with secret "database/creds/toutbet-app" }}postgresql+psycopg://{{ .Data.username }}:{{ .Data.password }}@postgres:5432/toutbet{{ end }}
  ```

Limite connue : la configuration est lue une seule fois au démarrage. Après une rotation, il faut redémarrer les instances une par une (l'agent Vault peut le déclencher), ou faire relire la configuration, ce que l'application ne sait pas encore faire.

## 4. Ce que gagne un attaquant, secret par secret

| Secret | Ce qu'il permet s'il fuit | Rotation |
|---|---|---|
| `JWT_SECRET` | Forger des jetons. Seul il ne suffit pas : chaque jeton doit correspondre à une session active en base avec le bon `jti`. Avec un jeton volé, l'attaquant prolonge cette session au-delà de 15 min ; s'il lit aussi la table `sessions`, il prend toutes les sessions actives. | Nouvelle clé : tous les jetons tombent, chacun se reconnecte. Avec `kid`, rotation planifiée sans coupure. |
| `RECEIPT_SIGNING_KEY` | Signer de faux reçus de mise, utiles pour réclamer une mise qui n'existe pas. Le grand livre chaîné reste la référence et `verify-ledger` ne connaît pas les reçus. | Nouvelle clé et nouveau `key_id`. Les reçus signés par l'ancienne clé après la date de fuite ne valent plus rien, on les compare au grand livre. |
| `PSP_WEBHOOK_SECRET` | Le plus grave pour l'argent : créer un dépôt via l'API puis forger le webhook « paiement réussi » crédite le portefeuille sans payer. Forger un `kyc.verified` pour son propre compte ouvre les retraits. | Nouveau secret chez le PSP, déployé, puis l'ancien révoqué. Chercher les dépôts crédités sans paiement réel dans le rapprochement avec le PSP. |
| `PSP_API_KEY` | Appeler le PSP en notre nom : lire les données des clients chez le PSP, créer des sessions de paiement ou des virements selon les droits de la clé. | Deuxième clé chez le PSP, déploiement, révocation de l'ancienne. Restreindre la clé aux seules opérations utilisées. |
| `APP_DB_PASSWORD` | Il faut d'abord atteindre la base (réseau interne, aucun port publié). Ensuite : lecture de toutes les données personnelles et des hash de mots de passe, modification des soldes. Le grand livre ne peut pas être modifié ni effacé, mais on peut y ajouter des écritures. Équivaut à compromettre l'application. | `ALTER ROLE toutbet_app PASSWORD ...` et redéploiement ; avec Vault, identifiants dynamiques qui expirent seuls. Puis `toutbet-admin verify-ledger`. |
| `POSTGRES_OWNER_PASSWORD` | Contrôle total de la base : DDL, suppression des tables, réécriture du grand livre et du journal d'audit. | `ALTER ROLE toutbet_owner PASSWORD ...`. Il ne sert qu'à `init-db`, on peut le changer à chaque migration. |

Les trois valeurs gardées dans `.gitleaksignore` ne sont pas des secrets : deux clés d'idempotence (des UUID) dans `test_as14_collusion.py` et `test_as15_repudiation.py`, et `bm90LTMyLWJ5dGVz` dans `test_settings.py`, le base64 de « not-32-bytes » qui sert à tester le refus d'une graine trop courte.
