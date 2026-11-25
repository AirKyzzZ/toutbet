# ToutBet' : steering

Version 1.0 du 2026-11-25. Groupe : Maxime Mansiet, Simon Godard (EPSI, cours « Sécurité pour les devs », atelier 1).

Ce dossier `specs/` suit le workflow spec-driven vu en cours : steering (ce fichier), exigences (`requirements.md`), conception (`design.md`), menaces (`threats.md`), tâches (`tasks/TASKS.md`). Le prompt de génération et la revue de sécurité sont historisés dans `docs/prompts.md`.

## Mission

ToutBet' permet à des particuliers de parier entre amis sur des événements du quotidien, par exemple « Matis va-t-il réussir son créneau ? ». Un bookie crée le pari et invite des parieurs. L'application séquestre les mises, puis paie les gagnants une fois le résultat validé, après avoir prélevé la commission du bookie et celle de la plateforme.

La plateforme détient l'argent des joueurs : un résultat truqué ou un solde faux détruit la confiance plus vite qu'une fonctionnalité manquante. La sécurité fait donc partie du produit dès le MVP.

## Périmètre du MVP

Inclus :

- compte utilisateur (inscription, connexion, déconnexion), tout utilisateur peut devenir bookie en créant un pari ;
- portefeuille en centimes, dépôt via la page de paiement hébergée d'un PSP de test, retrait après vérification d'identité (KYC simulé chez le PSP) ;
- paris privés à deux issues (OUI / NON), invitation par lien à usage unique, mise minimum optionnelle ;
- séquestre, clôture automatique avant l'événement, résultat proposé par le bookie, fenêtre de contestation de 24 h, arbitrage par un modérateur, règlement automatique ;
- grand livre append-only chaîné par hash et reçus de mise signés ;
- front web mobile-first minimal (HTML, CSS, JavaScript sans framework) servi par l'API.

Hors périmètre :

- argent réel, agrément ANJ et obligations LCB-FT : le MVP tourne uniquement en bac à sable ;
- paris publics, paris à plus de deux issues, cotes fixes, chat, notifications ;
- réinitialisation de mot de passe, vérification de l'email, MFA (à faire après le MVP, en commençant par les modérateurs) ;
- application mobile native, mode hors ligne ;
- déploiement multi-instance (les compteurs de limitation de débit restent en mémoire).

## Rôles et privilèges

| Rôle | Peut | Ne peut pas |
|---|---|---|
| Parieur | déposer, rejoindre un pari sur invitation, miser, contester, retirer après KYC | voir un pari où il n'est pas invité |
| Bookie (parieur qui a créé le pari) | inviter, annuler tant que le pari est ouvert, proposer le résultat | miser sur son propre pari, modifier les paramètres du pari |
| Modérateur (personnel de la plateforme) | consulter les litiges, trancher avec une justification | miser, créer un pari, être créé via l'API |
| PSP (système) | confirmer paiements, KYC et virements par webhook signé | créditer un compte sans signature valide |
| Planificateur (système) | clôturer, régler, escalader les paris sans résultat | être déclenché par une requête HTTP |

## Principes de sécurité

1. Moindre privilège dès la conception : rôle relu en base à chaque requête, compte de base de données sans droit de modifier le grand livre, conteneur non root, aucune route d'administration HTTP.
2. Refus par défaut : toute route exige authentification et autorisation, sauf une liste fermée de routes publiques.
3. Ne jamais faire confiance au client : montants, dates, rôles et statuts sont validés ou recalculés côté serveur. Le retour du navigateur depuis le PSP ne prouve pas un paiement.
4. Argent exact et traçable : entiers en centimes, débit conditionnel atomique, clés d'idempotence, grand livre chaîné, conservation des montants vérifiée à chaque règlement.
5. Minimisation : aucune donnée de carte chez nous (périmètre PCI-DSS SAQ-A), aucune pièce d'identité chez nous (KYC délégué au PSP).
6. Échec en sécurité : erreurs génériques, refus de démarrer sans secret valide, PSP indisponible = opération refusée et aucun crédit.
7. 1 menace = 1 ticket : chaque ligne de `threats.md` a son ticket `SEC-` dans `tasks/TASKS.md` et au moins un test.
8. Code généré par IA = code à relire : specs et prompts versionnés en markdown, revue de sécurité humaine avant tout commit du code généré.

## Confidentialité

- Données collectées : email, pseudonyme, date de naissance, hash du mot de passe, statut KYC, mouvements financiers. Rien d'autre.
- Les autres joueurs voient le pseudonyme et les totaux par issue, jamais l'email ni les mises individuelles.
- Les logs ne contiennent ni mot de passe, ni jeton, ni secret, ni donnée de carte, ni IBAN. L'utilisateur y apparaît par son identifiant interne.
- Conservation : logs applicatifs 90 jours. À la suppression d'un compte, les données personnelles sont anonymisées et les écritures financières conservées pour la durée légale.

## Indicateurs et objectifs

| Indicateur | Objectif | Mesure |
|---|---|---|
| Disponibilité de l'API | ≥ 99,5 % par mois | sonde `/healthz` |
| Latence p95 d'une mise | < 300 ms à 200 mises/s sur un même pari | Locust (atelier 2) |
| Erreurs 5xx | < 0,1 % des requêtes, aucune pendant un test de charge (hors 429) | logs, Locust |
| Solde négatif ou écart grand livre / soldes | 0 | contrainte `CHECK`, `toutbet-admin verify-ledger` |
| Abuse stories automatisées | 100 % de celles testables via l'API | `tests/abuse/` |
| Vulnérabilités critiques ou hautes ouvertes | 0 au merge, correction sous 7 jours | Semgrep (ateliers 2 et 3) |
| Secrets dans le dépôt | 0 | gitleaks, Semgrep |

## Definition of Done

Une tâche est terminée quand :

- ses tests passent, y compris ceux des abuse stories liées ;
- `ruff`, `mypy` et `pytest` sont verts ;
- aucune dépendance n'est ajoutée sans être verrouillée par hash dans `uv.lock` ;
- l'autre membre du binôme a fait la revue de sécurité (checklist de `docs/prompts.md`).

## Suite prévue

- Atelier 2 : Semgrep (avec règles maison, par exemple toute modification de solde hors de `services/money.py`), ZAP et ffuf sur la création de paris, abuse stories automatisées, Locust, test de concurrence sur le solde.
- Atelier 3 : cartographie de la chaîne de génération, SBOM, pipeline GitHub Actions, stratégie de secrets.
- Atelier 4 : matrice des droits de l'équipe, règles d'alerte sur les événements de sécurité de `design.md`, plan de chaos (PSP lent, base indisponible) avec les interrupteurs de fonctionnalités.
