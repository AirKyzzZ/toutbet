# Semgrep, avant et après

Premier scan sur le code tel que généré par P1 (commit `d83ec21`), scan final après les corrections de l'atelier 2, le 2026-11-25.

## Avant / après

| Scan | Commande | Avant | Après |
|---|---|---|---|
| Plateforme (code + supply chain, 145 981 règles) | `semgrep ci` | 0 alerte (`avant.json`) | 0 alerte (`apres.json`) |
| Packs publics sur `src`, `deploy`, Docker | `semgrep scan --config p/default --config p/owasp-top-ten --config p/python --config p/secrets --config p/docker-compose --config p/dockerfile --config p/javascript src tests deploy Dockerfile docker-compose.yml` | 0 (`avant-packs.json`) | 0 (`apres-packs.json`) |
| Mêmes packs en incluant `tests/` | même commande avec `--x-ignore-semgrepignore-files` sur `tests` | 15 (`avec-tests-avant.json`) | 6, justifiées plus bas (`avec-tests-apres.json`) |
| Nos 3 règles (`.semgrep/toutbet.yml`) | `semgrep scan --config .semgrep/toutbet.yml src tests` | règles pas encore écrites | 0 dans `src`, 3 voulues dans `tests` (`regles-personnalisees.json`) |

Les 22 erreurs du JSON des packs sont des règles JavaScript qui demandent le moteur Pro, pas des alertes.

Résultats sur la plateforme : https://semgrep.dev/orgs/mxmnst-personal-org/findings?repo=AirKyzzZ/toutbet&ref=main

## Le dossier tests/

Le `.semgrepignore` par défaut de Semgrep exclut `tests/`, et la plateforme a aussi `tests/` dans ses chemins ignorés : ni `semgrep ci` ni le scan des packs ne l'avaient regardé. On l'a scanné à part avec les packs publics, 15 alertes :

- 9 `len-all-count` : `len(db.scalars(select(X)).all())` charge toutes les lignes pour les compter. Pas un problème de sécurité, mais c'est vrai et facile à corriger : remplacé par `select(func.count())` (commit `test: count rows with sql count instead of loading them`).
- 6 restantes, gardées parce qu'elles sont le but du test :
  - `jwt-python-none-alg` dans `test_as01_account_takeover.py` : on forge un jeton `alg: none` pour prouver que l'API le refuse ;
  - `jwt-python-hardcoded-secret` et `python-pyjwt-hardcoded-secret` (même fichier) : on signe avec une mauvaise clé pour prouver que la signature est vérifiée ;
  - les deux mêmes dans `test_redaction.py` : un JWT bidon dont on vérifie qu'il est masqué dans les logs ;
  - `hardcoded-password-default-argument` dans `support.py` : mot de passe d'un compte de test sur une base temporaire.

Aucune de ces valeurs n'est un vrai secret, elles ne servent que dans la suite de tests.

## Corrections venues de l'étape 2

Les scans statiques n'ont rien trouvé dans `src/`, mais l'exploration dynamique (dossier `../dast/`) a trouvé de vrais défauts, corrigés dans le même atelier : deux erreurs 500 (dates hors plage en UTC, caractère NUL sur PostgreSQL), un caractère saisi recopié dans les erreurs 422, le log d'accès uvicorn réactivé malgré `--no-access-log`, et l'en-tête COEP manquant. Le scan final tourne sur ce code corrigé et reste à 0.

Règles personnalisées : `regles-personnalisees.md`.
