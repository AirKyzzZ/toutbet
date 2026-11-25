# Atelier 4 : résilience, identités et observabilité

Les ateliers 1 à 3 ont construit et protégé l'application et sa chaîne de production. Ici on regarde qui a accès à quoi, ce qu'on surveille, et ce qui se passe quand une dépendance casse.

## Fichiers

| Étape | Fichier | Contenu |
|---|---|---|
| 1 | [`iam.md`](iam.md) | matrice des privilèges (5 rôles, zones de l'atelier 3, menaces `T-SC*`), workflow d'approbation des déploiements, check-lists de révocation par rôle, attestation trimestrielle |
| 2 | [`supervision.md`](supervision.md) | événements réellement journalisés, 23 règles d'alerte (nom, condition, sévérité, réponse), logs à croiser sur un scénario de jeton de modérateur volé, cartes et mots de passe hors des logs, conservation |
| 3 | [`chaos.md`](chaos.md) | PSP, PostgreSQL et worker perturbés, mode dégradé sans création de paris, conditions d'arrêt, fiche d'expérience PSP à 10 s et résultat du run local |
| 3 | [`chaos/`](chaos/) | script de l'expérience (`psp_latency.py`) et mesures brutes |

## Remise

| Demandé par le sujet | Où |
|---|---|
| Tableau des droits | `iam.md` §1 |
| Schéma du workflow d'approbation des déploiements | `iam.md` §2 |
| Règles d'alerte : nom, condition, sévérité | `supervision.md` §2 |
| Fiche de test d'une expérience de chaos : hypothèse, expérience, résultat attendu, plan de repli | `chaos.md` §4 |

## Code ajouté pendant l'atelier

Chaque point a ses tests et passe `pytest`, `ruff`, `mypy`, `semgrep --test` et la suite E2E.

| Changement | Pourquoi | Fichiers |
|---|---|---|
| Événement `money.deposit_failed` | la règle « 3 échecs de paiement de suite » n'avait rien à compter | `src/toutbet/services/deposits.py` |
| `auth.token_rejected` avec `reason=bad_signature` | distinguer une signature forgée d'un jeton expiré | `src/toutbet/security/tokens.py`, `authz.py` |
| `toutbet-admin check-alerts` | évaluer 5 règles sur les vrais logs JSON | `src/toutbet/alerts.py`, `cli.py` |
| `toutbet-admin revoke-access` | couper toutes les sessions d'un compte et retirer le rôle modérateur en une commande | `src/toutbet/cli.py`, `security/sessions.py` |
| 4e règle Semgrep `toutbet-sensitive-data-in-logs` | interdire secrets, données de carte et f-strings dans les appels de log | `.semgrep/toutbet.yml`, `.semgrep/tests/toutbet.py` |
| Messages clairs des interrupteurs, `bet_creation_enabled` dans `/api/me`, bandeau dans le front | mode dégradé quand la création de paris est coupée | `src/toutbet/api/deps.py`, `api/auth.py`, `static/` |
| Latence réglable du PSP simulé (`MOCK_PSP_LATENCY_MS`, `MOCK_PSP_LATENCY_DURATION_S`) | injecter la panne de l'expérience | `src/mock_psp/` |

Ce qui reste écrit sans être fait : protection de `main`, CODEOWNERS, environnements GitHub et job de déploiement OIDC, coffre de secrets, outil de logs et alertes branchées dans un vrai SIEM, Game Day en staging. Tout ça demande des réglages GitHub ou une infrastructure qu'on n'a pas.
