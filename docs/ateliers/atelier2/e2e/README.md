# Atelier 2, étape 3 : abuse stories automatisées de bout en bout

Les 86 tests de `tests/abuse/` appellent l'API dans le même processus (TestClient, horloge simulée). Ici on ajoute 33 tests Playwright qui passent par un vrai navigateur et une vraie pile : Chromium headless en vue mobile (375×812, `fr-FR`, `Europe/Paris`), l'application lancée avec uvicorn, le PSP simulé, une base SQLite temporaire et des secrets générés à chaque lancement.

## Lancer les tests

```sh
uv sync --group e2e
uv run --group e2e playwright install chromium
uv run --group e2e pytest tests/e2e
```

Environ 30 secondes sur nos machines. `uv run pytest` seul ne lance pas ces tests (`--ignore=tests/e2e` dans `pyproject.toml`), la suite habituelle reste rapide.

En CI (GitHub Actions, sans Docker), il suffit de :

```yaml
- uses: astral-sh/setup-uv@v6
- run: uv sync --locked --group e2e
- run: uv run --group e2e playwright install --with-deps chromium
- run: uv run --group e2e pytest tests/e2e
```

## Ce que fait la pile de test

Tout est dans `tests/e2e/conftest.py` :

- la fixture `stack` (une par session) crée le schéma, génère `JWT_SECRET`, `RECEIPT_SIGNING_KEY`, `PSP_WEBHOOK_SECRET` et `PSP_API_KEY`, démarre le PSP simulé sur le port 8201 puis l'application sur le port 8200 (mêmes options que le `Dockerfile`), attend `/healthz`, et arrête tout à la fin. Les limites de débit y sont relevées pour que les tests ne se bloquent pas entre eux ;
- la fixture `strict_stack` démarre une application neuve sur le port 8202 avec les limites de production, une par test, pour les tests de 429 ;
- `TRUSTED_PROXY_IPS` vaut une adresse de documentation (`192.0.2.1`) : aucun client local ne peut fixer `X-Forwarded-For`, comme en production derrière Caddy ;
- chaque page enregistre les violations CSP (événement `securitypolicyviolation` et console) et les boîtes de dialogue JavaScript. Un test qui en provoque une échoue, même s'il ne teste pas le XSS ;
- à l'arrêt de la pile, le test échoue si le log serveur contient une erreur `error.unhandled`, le mot de passe de test ou un JWT.

Pour simuler le temps qui passe (clôture d'un pari, invitation expirée, début de l'événement), les tests modifient `closes_at`, `expires_at` ou `event_starts_at` directement dans la base temporaire. Le serveur garde sa vraie horloge et aucune route de test n'existe dans l'application. Le compte modérateur est créé de la même façon, comme le ferait `toutbet-admin create-moderator`.

Les requêtes trafiquées passent par `page.route` : le navigateur envoie la requête de l'interface et on modifie le corps ou l'URL au vol, comme le ferait un attaquant avec un proxy. Quand l'interface n'offre pas le bouton (le bookie qui mise sur son pari, par exemple), le test appelle `fetch` depuis la page avec le jeton de la session, comme depuis la console du navigateur.

## Parcours critique

`test_e2e_critical_journey_signup_deposit_bet_stake_logout` : le bookie s'inscrit, se connecte, dépose 20 € sur la page de paiement du PSP, fait vérifier son identité, crée le pari et un lien d'invitation. Un deuxième contexte navigateur ouvre le lien, crée son compte, rejoint le pari, dépose 20 € et mise 10 €. Les deux se déconnectent : l'ancien jeton est refusé par `/api/wallet`, `/api/me` et `/api/auth/refresh` (401), et ni les routes privées ni le bouton retour n'affichent de solde, de pseudo, d'email ou de titre de pari.

## Correspondance abuse stories et tests

Résultat : tout passe (33 E2E, 86 tests API d'abus).

| AS | Tests API (`tests/abuse/`) | Tests E2E (`tests/e2e/`) | Résultat |
|---|---|---|---|
| AS-01 force brute | `test_as01_account_takeover.py` (8) | `test_e2e_as01_login_flood_gets_429_before_password_check` (5 échecs puis 429 avec `Retry-After`, même avec un `X-Forwarded-For` changeant), `test_e2e_as01_forged_tokens_send_back_to_login` (`alg: none`, signature fausse, `exp` ou `role` modifiés), `test_e2e_as01_token_kept_out_of_local_storage_cookies_and_urls` | OK |
| AS-02 crédit sans payer | `test_as02_forged_webhook.py` (5) | `test_e2e_as02_coming_back_from_psp_without_paying_credits_nothing`, `test_e2e_as02_webhooks_forged_from_the_browser_credit_nothing` (sans signature, signature fausse, horodatage vieux de 10 min, rejeu x3 crédité une fois) | OK |
| AS-03 IDOR portefeuille | `test_as03_wallet_idor.py` (4) | `test_e2e_as03_other_players_deposit_and_wallet_stay_private`, `test_e2e_as03_acting_on_someone_elses_bet_returns_404` (mise redirigée vers le pari d'un autre, annulation, invitation, résultat, contestation : 404) | OK |
| AS-04 retrait frauduleux | `test_as04_fraudulent_withdrawal.py` (6) | `test_e2e_as04_withdrawal_needs_kyc_password_and_no_destination` (403 sans KYC, 422 avec `iban` ou `destination`, 401 sans mot de passe ou faux), `test_e2e_as04_stolen_token_useless_after_logout` | OK |
| AS-05 paramètres et XSS | `test_as05_bet_tampering.py` (6) | `test_e2e_as05_html_in_every_free_text_field_renders_as_text` (titre, description, justificatif, motif de contestation, justification du modérateur), `test_e2e_as05_no_csp_violation_on_any_page`, `test_e2e_as05_tampered_bet_parameters_rejected`, `test_e2e_as05_html_display_name_rejected_at_signup`, `test_e2e_as05_unbroken_title_keeps_the_page_phone_wide` | OK après correction (voir plus bas) |
| AS-06 pari privé | `test_as06_private_bet_intrusion.py` (5) | `test_e2e_as06_used_invitation_link_cannot_be_replayed`, `test_e2e_as06_expired_and_guessed_invitation_links_refused`, `test_e2e_as06_invitation_brute_force_gets_429` (10 x 404 puis 429) | OK |
| AS-07 mise trafiquée, sur-dépense | `test_as07_stake_tampering.py` (6) | `test_e2e_as07_tampered_stakes_rejected_and_nothing_debited` (négatif, 0, 99, 50 001, 10.5, chaîne, booléen, 10^15, issue inconnue, champs en plus), `test_e2e_as07_replayed_stake_debits_once`, `test_e2e_as07_stake_after_closing_refused_before_worker_runs` ; en HTTP sur PostgreSQL : `tests/load/test_live_overspend.py` | OK |
| AS-08 parier en connaissant le résultat | `test_as08_past_posting.py` (4) | `test_e2e_as08_client_timestamp_and_bet_edits_refused` (horodatage client 422, `PUT`/`PATCH`/`DELETE` sur pari et mise 405) | OK |
| AS-09 faux résultat | `test_as09_false_result.py` (6) | `test_e2e_as09_only_the_bookie_proposes_and_only_once` ; le justificatif avec URL n'est jamais chargé par le navigateur (vérifié dans le test XSS) | OK |
| AS-10 contestations abusives | `test_as10_contest_flood.py` (5) | `test_e2e_as10_contest_needs_a_stake_and_happens_once` (sans mise 403, motif en espaces 422, deuxième contestation 409), `test_e2e_as10_signup_flood_gets_429` | OK |
| AS-11 payé deux fois | `test_as11_double_settlement.py` (3) | aucun, voir plus bas | OK (API) |
| AS-12 se promouvoir modérateur | `test_as12_privilege_escalation.py` (5) | `test_e2e_as12_role_and_kyc_fields_rejected_at_signup`, `test_e2e_as12_player_cannot_open_moderation` | OK |
| AS-13 saturation | `test_as13_event_flood.py` (5) | `test_e2e_as13_write_flood_gets_429` (19 invitations puis 429), `test_e2e_as13_oversized_body_gets_413` ; charge : Locust, `docs/ateliers/atelier2/load/` | OK |
| AS-14 collusion | `test_as14_collusion.py` (5) | `test_e2e_as14_bookie_cannot_stake_or_join_own_bet`, `test_e2e_as14_moderator_cannot_create_bet_or_join` ; la décision du modérateur visible par le parieur est vérifiée dans le test XSS | OK |
| AS-15 nier avoir parié | `test_as15_repudiation.py` (3) | `test_e2e_as15_stake_receipt_verifies_with_public_key` (reçu Ed25519 vérifié avec la clé publique, reçu modifié refusé) | OK |
| AS-16 faire parler l'application | `test_as16_information_leak.py` (10) | `test_e2e_as16_errors_and_paths_leak_nothing` (JSON cassé, injection SQL, `../`, `/docs`) ; contrôle du log serveur à l'arrêt de chaque pile | OK |

## Ce qu'on n'automatise pas dans le navigateur

- **AS-11 double règlement** : aucune route HTTP ne déclenche un règlement, seuls le worker et la décision du modérateur le font. Provoquer deux règlements simultanés demande de piloter le worker, pas le navigateur. Les tests API le font avec des threads, et un test par propriété vérifie les arrondis.
- **AS-09 escalade après 72 h** et **fenêtre de contestation de 24 h** : il faut faire avancer l'horloge du serveur et lancer le worker. Les tests API utilisent une horloge simulée.
- **AS-01 verrouillage après 10 échecs en 15 minutes** : depuis une seule IP, la limite de 5 tentatives par minute coupe avant le 10e échec. Il faudrait attendre plus de deux minutes ou passer par un proxy de confiance. Testé côté API.
- **AS-10 seuil pondéré par les mises** et **AS-02 montant différent du dépôt** : ce sont des calculs serveur ou des webhooks envoyés par le PSP, le navigateur n'y change rien.
- **AS-15 grand livre modifié en base** et **AS-16 secrets dans les logs** : c'est côté serveur. On le vérifie quand même en partie : la pile E2E échoue si son log contient un mot de passe ou un JWT, et `verify-ledger` tourne après chaque test de charge.
- **AS-07 et AS-04 en concurrence** : un navigateur n'envoie pas 50 requêtes à la même milliseconde. C'est fait en HTTP avec asyncio dans `tests/load/test_live_overspend.py`, voir `docs/ateliers/atelier2/load/solde-negatif.md`.

## Défaut trouvé et corrigé

Le test XSS n'arrivait pas à cliquer sur « Proposer le résultat » en vue mobile. Un titre de pari avec un long mot sans espace débordait de la carte : la page faisait 517 px de large avec le titre `<img src=x onerror=alert('titre')>Matis<script>alert(1)</script>`, et 7 303 px avec 140 « M », sur un écran de 375 px. Les boutons se retrouvaient recouverts par d'autres éléments. Un bookie pouvait donc casser la page de son pari pour tous les participants sur téléphone. Correction : `overflow-wrap: anywhere` sur `.view` (commit `fix: wrap long unbroken words so bet pages fit a phone screen`), test de régression `test_e2e_as05_unbroken_title_keeps_the_page_phone_wide`.

## Remarques sur le front, non corrigées

Aucune n'expose de donnée, on les note pour la suite :

- `.nav { display: flex }` l'emporte sur l'attribut `hidden` : le menu et le bouton « Déconnexion » restent visibles quand on est déconnecté ;
- les messages de succès (« Mise enregistrée. Reçu signé… », « Invitation acceptée. ») sont effacés aussitôt, parce que `go()` vers la même route relance un rendu qui vide le message ;
- un lien d'invitation ouvert dans un onglet déjà sur ToutBet' (seul le fragment change) n'est accepté qu'après un rechargement ;
- le routeur n'annule pas un rendu en cours : en naviguant très vite, une vue plus ancienne peut remplacer la nouvelle. Les tests attendent donc chaque vue avant d'agir ;
- un mauvais mot de passe au retrait renvoie 401, et le front efface alors le jeton : l'utilisateur est déconnecté de l'interface alors que sa session serveur est toujours valide.
