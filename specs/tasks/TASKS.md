# ToutBet' : tâches d'implémentation

Version 1.0 du 2026-11-25. Tâches atomiques à réaliser dans l'ordre. Chaque tâche indique ses entrées, ce qui est attendu, le critère « fait quand » avec le test qui le prouve, et ses liens vers `requirements.md` (US, AS), `threats.md` (T) et `design.md`.

Conventions de tests :

- `tests/api/test_usNN_<sujet>.py` : critères d'acceptation, une fonction `test_usNN_M_...` par critère `US-NN.M` ;
- `tests/abuse/test_asNN_<sujet>.py` : abuse stories, fonctions `test_asNN_...` ;
- `tests/unit/` et `tests/security/` : logique pure et contrôles transverses.

Une tâche n'est terminée que si la Definition of Done de `STEERING.md` est respectée.

## Lot 0 : socle

### TASK-01 Squelette du projet

- Entrées : `STEERING.md`, `design.md` §1, `.gitignore` existant.
- À faire : `pyproject.toml` (Python 3.13, dépendances de `design.md` §1, scripts `toutbet-admin`, `toutbet-worker`, `mock-psp`), `uv.lock`, arborescence `src/toutbet/` et `tests/`, configuration ruff, mypy strict et pytest, fabrique `create_app(settings)`, route `GET /healthz`.
- Fait quand : `uv sync --locked`, `uv run ruff check`, `uv run ruff format --check`, `uv run mypy src` et `uv run pytest` passent, dont `tests/api/test_health.py::test_healthz_returns_ok_without_details`.
- Liens : T-E06.

### TASK-02 Configuration, secrets et horloge

- Entrées : `design.md` §13, TB5.
- À faire : `Settings` avec pydantic-settings, secrets obligatoires sans valeur par défaut (32 octets minimum), message d'erreur qui nomme la variable sans afficher sa valeur, interrupteurs de fonctionnalités, horloge injectable `Clock` (UTC, avec fuseau), `.env.example` factice, commande `toutbet-admin gen-secrets`.
- Fait quand : `tests/unit/test_settings.py::test_settings_refuse_missing_or_weak_secrets` et `::test_settings_error_does_not_print_secret_value` passent, `git check-ignore .env` répond `.env`.
- Liens : T-I05, T-D01, TB5.

### TASK-03 Base de données et modèles

- Entrées : `design.md` §5 et §7.4.
- À faire : modèles SQLAlchemy 2 typés (`Mapped[]`), contraintes du §5, pragmas SQLite (`foreign_keys`, WAL, `busy_timeout`) et transactions d'écriture en `BEGIN IMMEDIATE`, dates UTC relues avec leur fuseau sur SQLite, commande `toutbet-admin init-db`, script `deploy/postgres/grants.sql`.
- Fait quand : `tests/unit/test_db_constraints.py` passe (solde négatif, mise nulle, `closes_at` après `event_starts_at`, double mise : tous refusés par la base), sur SQLite et avec `DATABASE_URL` PostgreSQL.
- Liens : T-T02, T-T08, TB3.

### TASK-04 Argent, grand livre et journal d'audit

- Entrées : `design.md` §7.1 et §7.4.
- À faire : `services/money.py` (`debit_wallet`, `credit_wallet`, seul code qui modifie `balance_cents`), `services/ledger.py` (`append_ledger_entry`, `append_audit_event`, verrou sur `chain_heads`), commande `toutbet-admin verify-ledger`.
- Fait quand : `tests/unit/test_ledger.py::test_chain_links_each_entry_to_previous`, `::test_verify_ledger_detects_modified_entry`, `::test_verify_ledger_detects_balance_mismatch` et `tests/unit/test_money.py::test_debit_refused_when_balance_insufficient` passent.
- Liens : T-T02, T-T08, T-R01, TB3.

### TASK-05 Erreurs, request_id et logs

- Entrées : `design.md` §9 (format d'erreur) et §12.
- À faire : middleware `request_id` (généré côté serveur, renvoyé dans `X-Request-ID`), gestionnaires d'erreurs au format unique, erreurs 422 sans les valeurs reçues, logger JSON sur la sortie standard avec filtre de masquage, fonction `log_event(nom, **champs)`.
- Fait quand : `tests/unit/test_redaction.py` et les tests SEC-I02, SEC-I03 et SEC-R04 passent.
- Liens : T-I02, T-I03, T-R04, TB6.

### TASK-06 En-têtes, CORS, hôtes et taille des requêtes

- Entrées : `design.md` §11.
- À faire : middleware d'en-têtes de sécurité (API et statiques), CORS en liste blanche, `TrustedHostMiddleware`, limite de corps à 64 Ko.
- Fait quand : `tests/security/test_headers.py::test_security_headers_on_api_and_static`, `::test_cors_rejects_unknown_origin`, `::test_unknown_host_rejected` et `tests/abuse/test_as13_event_flood.py::test_as13_oversized_body_returns_413` passent.
- Liens : T-I06, T-T09, T-D03, TB1, TB2.

### TASK-07 Limitation de débit

- Entrées : `design.md` §9 (limites).
- À faire : slowapi, clé = identifiant utilisateur si authentifié sinon IP client (fiable seulement derrière `TRUSTED_PROXY_IPS`), limites configurables, réponse 429 au format d'erreur avec `Retry-After`, événement `rate_limit.exceeded`, limite de connexion appliquée avant toute lecture en base ou calcul de hash.
- Fait quand : les tests SEC-S01, SEC-S06, SEC-D01 et SEC-E03 passent, avec un compteur remis à zéro entre deux tests.
- Liens : T-S01, T-S06, T-D01, T-D02, T-E03.

### TASK-08 Idempotence des opérations financières

- Entrées : `design.md` §8.3.
- À faire : helper utilisé par les dépôts, retraits et mises, qui enregistre la clé et l'empreinte du corps dans la transaction de l'opération et rejoue la réponse stockée.
- Fait quand : `tests/unit/test_idempotency.py` et les tests SEC-T03 passent.
- Liens : T-T03.

### TASK-09 Authentification et sessions

- Entrées : US-01, `design.md` §10.
- À faire : inscription, connexion, renouvellement, déconnexion, `GET /api/me`, dépendance `current_user` (JWT, session, `jti`, rôle relu en base), verrouillage temporaire, hash factice pour email inconnu.
- Fait quand : `tests/api/test_us01_auth.py` (un test par critère US-01.x) et `tests/abuse/test_as01_account_takeover.py` passent.
- Liens : US-01, AS-01, T-S01, T-S02, T-S04, T-I04, T-D02, T-E01.

## Lot 1 : fonctionnalités

### TASK-10 Client PSP et mock PSP

- Entrées : `design.md` §8.1 et §8.4, TB4.
- À faire : protocole `PspClient` (`create_checkout`, `create_kyc_session`, `create_payout`), implémentation `httpx` avec timeouts 2 s / 5 s et URL issue de la configuration, faux client pour les tests, fonction `sign_webhook` partagée ; application séparée `mock_psp` (dev et tests uniquement) avec page de paiement sans aucun champ de carte (boutons « Payer » / « Refuser »), page KYC, API de virement, webhooks signés.
- Fait quand : `tests/unit/test_psp_client.py::test_timeouts_are_configured` et `::test_base_url_comes_from_settings_only` passent, `uv run mock-psp` démarre.
- Liens : T-D05, T-E05, TB4.

### TASK-11 Dépôts et webhook PSP

- Entrées : US-02, `design.md` §8.1 et §8.4.
- Fait quand : `tests/api/test_us02_deposits.py` et `tests/abuse/test_as02_forged_webhook.py` passent.
- Liens : US-02, AS-02, T-S03, T-T03, T-D05.

### TASK-12 Portefeuille et historique

- Entrées : US-03.
- Fait quand : `tests/api/test_us03_wallet.py` et `tests/abuse/test_as03_wallet_idor.py` passent.
- Liens : US-03, AS-03, T-I01, T-I07.

### TASK-13 KYC et retraits

- Entrées : US-04, `design.md` §8.4.
- Fait quand : `tests/api/test_us04_withdrawals.py` et `tests/abuse/test_as04_fraudulent_withdrawal.py` passent.
- Liens : US-04, AS-04, T-S04, T-S05, T-T02, T-D05.

### TASK-14 Paris : création, lecture, liste, annulation

- Entrées : US-05, `design.md` §6.
- Fait quand : `tests/api/test_us05_bets.py` et `tests/abuse/test_as05_bet_tampering.py` passent.
- Liens : US-05, AS-05, T-T04, T-T06, T-T07, T-T09, T-I07.

### TASK-15 Invitations

- Entrées : US-06.
- Fait quand : `tests/api/test_us06_invitations.py` et `tests/abuse/test_as06_private_bet_intrusion.py` passent.
- Liens : US-06, AS-06, T-E03, T-E04, T-I01.

### TASK-16 Mises et reçus signés

- Entrées : US-07, `design.md` §7.1, §7.5 et §8.2.
- Fait quand : `tests/api/test_us07_stakes.py`, `tests/abuse/test_as07_stake_tampering.py` (dont le test de concurrence) et `tests/abuse/test_as15_repudiation.py` passent.
- Liens : US-07, AS-07, AS-15, T-T01, T-T02, T-T03, T-R01, T-E04.

### TASK-17 Proposition du résultat

- Entrées : US-09, `design.md` §6.
- Fait quand : `tests/api/test_us09_result.py` et `tests/abuse/test_as09_false_result.py` passent.
- Liens : US-09, AS-09, T-T05, T-R02, T-E02, T-E05.

### TASK-18 Contestations

- Entrées : US-10, `design.md` §6.
- Fait quand : `tests/api/test_us10_contests.py` et `tests/abuse/test_as10_contest_flood.py` passent.
- Liens : US-10, AS-10, T-D04, T-S06.

### TASK-19 Règlement

- Entrées : US-11, `design.md` §7.2 et §7.3.
- À faire : fonction pure `compute_settlement(...)` qui renvoie le plan de crédits et de commissions, puis `apply_settlement(...)` qui l'applique dans une transaction après la transition conditionnelle.
- Fait quand : `tests/unit/test_settlement.py::test_settlement_worked_example`, `::test_settlement_worked_example_with_forfeited_bookie_fee`, `::test_settlement_conserves_money` (hypothesis), `::test_refund_when_one_side_is_empty`, ainsi que `tests/api/test_us11_settlement.py` et `tests/abuse/test_as11_double_settlement.py` passent.
- Liens : US-11, AS-11, T-T10, T-T08.

### TASK-20 Planificateur

- Entrées : US-08, US-10.4, `design.md` §6 et §7.4.
- À faire : `tick(session, clock)` (clôture, escalade à 72 h, règlement en fin de fenêtre), boucle `toutbet-worker` toutes les 30 s, événement `ledger.head` toutes les 5 min.
- Fait quand : `tests/api/test_us08_closing.py`, `tests/abuse/test_as08_past_posting.py` et `tests/unit/test_worker.py::test_tick_twice_is_harmless` passent.
- Liens : US-08, AS-08, T-T06, T-T10, T-D06, T-R04.

### TASK-21 Modération et CLI d'administration

- Entrées : US-12, `design.md` §10.
- Fait quand : `tests/api/test_us12_moderation.py`, `tests/abuse/test_as12_privilege_escalation.py`, `tests/abuse/test_as14_collusion.py` et `tests/unit/test_cli.py::test_create_moderator_reads_password_from_stdin` passent.
- Liens : US-12, AS-12, AS-14, T-E01, T-E02, T-E04, T-R03.

### TASK-22 Front mobile-first

- Entrées : US-01 à US-12, `design.md` §10 et §11.
- À faire : `static/index.html`, `static/app.css`, modules `static/js/*.js`, routes par fragment (`#/login`, `#/wallet`, `#/bets`, `#/bets/<id>`, `#/invite/<jeton>`, `#/moderation`), affichage par `textContent` uniquement, manifeste PWA, aucun script ni style inline.
- Fait quand : `tests/security/test_static_assets.py::test_html_has_no_inline_script_or_style` et `::test_js_has_no_dangerous_sinks` passent, et le parcours inscription, dépôt (mock PSP), création de pari, invitation, mise, résultat, règlement fonctionne dans un navigateur en largeur 375 px sans erreur CSP dans la console.
- Liens : T-T09, T-S04.

## Lot 2 : livraison

### TASK-23 Docker et docker compose

- Entrées : `design.md` §14.
- À faire : `Dockerfile` multi-étapes non root, `.dockerignore`, `docker-compose.yml` (proxy Caddy, app, worker, postgres, init-db, mock-psp en profil `dev`), `deploy/Caddyfile`.
- Fait quand : `docker compose --profile dev up` permet le parcours complet sur `https://localhost`, `docker compose exec app id -u` ne renvoie pas 0, une écriture dans `/app` échoue, et `tests/security/test_pg_grants.py::test_pg_app_role_cannot_update_ledger` passe contre le PostgreSQL du compose.
- Liens : T-E06, T-T08, T-I05, TB2, TB3.

### TASK-24 README et traçabilité

- Entrées : toutes les tâches.
- Fait quand : `README.md` contient l'installation, la génération des secrets, le lancement (uv et docker compose), les commandes de test et d'administration, une table AS vers fichier de test, une table T vers SEC vers tests, et une section « Écarts et décisions » qui liste chaque écart aux specs.
- Liens : toutes les AS et T.

## Lot 3 : tickets de sécurité (1 menace = 1 ticket)

Chaque ticket implémente la mitigation de sa menace dans `threats.md` et n'est fermé que si ses tests passent.

| Ticket | Menace | À implémenter | Fait quand (tests) | Tâche |
|---|---|---|---|---|
| SEC-S01 | T-S01 force brute | 5 / min par IP sur la connexion avant base et hash, verrouillage 15 min après 10 échecs | `test_as01_login_rate_limited_per_ip`, `test_as01_spoofed_forwarded_for_does_not_bypass_limit`, `test_as01_account_locked_after_ten_failures` | TASK-07, TASK-09 |
| SEC-S02 | T-S02 JWT forgé | décodage PyJWT strict, claims obligatoires, session et `jti` vérifiés | `test_as01_tampered_token_rejected` (paramétré : `alg: none`, signature fausse, HS512, `exp` dépassé, `aud` faux, `sid` absent) | TASK-09 |
| SEC-S03 | T-S03 webhook forgé | HMAC sur le corps brut avant parsing, tolérance 5 min | `test_as02_unsigned_webhook_rejected`, `test_as02_bad_signature_rejected`, `test_as02_stale_timestamp_rejected` | TASK-11 |
| SEC-S04 | T-S04 vol de jeton | révocation à la déconnexion, rotation du `jti`, mot de passe pour retirer | `test_us01_5_token_rejected_after_logout`, `test_us01_6_old_token_rejected_after_refresh`, `test_as04_withdrawal_requires_password` | TASK-09, TASK-13 |
| SEC-S05 | T-S05 identité usurpée | KYC par webhook signé, aucun champ de destination, statut `verified` exigé | `test_as04_withdrawal_requires_verified_kyc`, `test_as04_destination_field_rejected`, `test_as04_kyc_status_cannot_be_self_assigned` | TASK-13 |
| SEC-S06 | T-S06 multi-comptes | 5 inscriptions / h par IP, email normalisé unique | `test_as10_registration_rate_limited_per_ip`, `test_as10_email_case_variants_are_one_account` | TASK-09 |
| SEC-T01 | T-T01 mise modifiée | aucune route de modification, état et échéance vérifiés dans la transaction | `test_as07_stake_after_close_rejected_before_worker_runs`, `test_as07_stake_cannot_be_modified_or_deleted` | TASK-16 |
| SEC-T02 | T-T02 double dépense | débit conditionnel, `CHECK`, `BEGIN IMMEDIATE` sur SQLite | `test_as07_concurrent_stakes_never_overspend`, `test_as04_concurrent_withdrawals_never_overspend`, sur SQLite et PostgreSQL | TASK-04, TASK-16 |
| SEC-T03 | T-T03 rejeu | helper d'idempotence, `event_id` unique | `test_as07_replayed_stake_debits_once`, `test_as07_reused_key_with_other_body_rejected`, `test_as07_missing_idempotency_key_rejected`, `test_as02_replayed_webhook_credits_once` | TASK-08, TASK-11 |
| SEC-T04 | T-T04 mass assignment | `extra="forbid"`, `StrictInt` bornés | `test_as05_commission_above_cap_rejected`, `test_as05_unknown_fields_rejected`, `test_as05_non_integer_amounts_rejected` (paramétré : `10.5`, `"1000"`, `true`, `-100`) | TASK-14 |
| SEC-T05 | T-T05 faux résultat | proposition unique, commission annulée si le modérateur infirme | `test_as09_result_cannot_be_proposed_twice`, `test_as09_overturned_result_forfeits_bookie_fee` | TASK-17, TASK-19, TASK-21 |
| SEC-T06 | T-T06 past-posting | validation et contrainte des dates, contrôle à chaque mise, aucune route de modification | `test_as08_closes_at_after_event_start_rejected`, `test_as08_stake_at_closes_at_rejected`, `test_as08_client_timestamp_rejected`, `test_as05_bet_cannot_be_modified` | TASK-14, TASK-16 |
| SEC-T07 | T-T07 injection SQL | filtres en `Literal`, aucun SQL concaténé | `test_as16_sql_injection_in_filters_rejected` | TASK-14 |
| SEC-T08 | T-T08 altération en base | chaîne SHA-256, droits PostgreSQL, `verify-ledger` | `test_as15_verify_ledger_detects_tampering`, `test_pg_app_role_cannot_update_ledger` (ignoré sur SQLite) | TASK-04, TASK-23 |
| SEC-T09 | T-T09 XSS stockée | `textContent`, CSP, aucun inline | `test_as05_html_title_returned_verbatim_with_csp`, `test_js_has_no_dangerous_sinks`, `test_html_has_no_inline_script_or_style` | TASK-06, TASK-22 |
| SEC-T10 | T-T10 double règlement | transition conditionnelle, plus fort reste, pas de route HTTP | `test_as11_concurrent_settlement_pays_once`, `test_as11_no_http_route_triggers_settlement`, `test_settlement_conserves_money` | TASK-19, TASK-20 |
| SEC-R01 | T-R01 perdant qui nie | reçu Ed25519, écriture avec session et requête | `test_as15_receipt_signature_verifies_with_public_key`, `test_as15_stake_entry_records_session_and_request` | TASK-16 |
| SEC-R02 | T-R02 bookie qui nie | événement chaîné `bet.result_proposed` | `test_as09_result_proposal_recorded_in_audit_chain` | TASK-17 |
| SEC-R03 | T-R03 modérateur qui nie | justification obligatoire, `moderation.decision` chaîné et visible | `test_as14_decision_requires_justification`, `test_as14_decision_recorded_and_visible_to_participants` | TASK-21 |
| SEC-R04 | T-R04 logs effacés | logs sur la sortie standard, `request_id` partout, `ledger.head` | `test_logs_are_json_lines_with_request_id`, `test_request_id_stored_in_ledger_entry`, `test_worker_emits_ledger_head` | TASK-05, TASK-20 |
| SEC-I01 | T-I01 IDOR | helpers d'accès objet qui renvoient 404 | `test_as03_other_user_deposit_returns_404`, `test_as03_object_access_matrix`, `test_as06_non_participant_cannot_read_or_stake` | TASK-12, TASK-14, TASK-15 |
| SEC-I02 | T-I02 stacktraces | gestionnaires génériques, documentation désactivée | `test_as16_unhandled_error_returns_generic_body`, `test_as16_validation_error_does_not_echo_input`, `test_as16_docs_disabled_outside_dev` | TASK-05 |
| SEC-I03 | T-I03 PII dans les logs | filtre de masquage | `test_as16_logs_never_contain_secrets` (parcours inscription, connexion, dépôt, webhook, retrait), `test_redaction_masks_card_numbers_and_jwts` | TASK-05 |
| SEC-I04 | T-I04 énumération | réponses identiques, hash factice | `test_as01_login_error_identical_for_unknown_email`, `test_as01_register_response_identical_for_existing_email` | TASK-09 |
| SEC-I05 | T-I05 secrets exposés | configuration fail-fast, `.gitignore`, `.dockerignore` | `test_settings_refuse_missing_or_weak_secrets`, `git check-ignore .env`, revue de `.env.example` | TASK-02, TASK-23 |
| SEC-I06 | T-I06 interception | HSTS, redirection HTTPS par Caddy | `test_security_headers_on_api_and_static`, `curl -I http://localhost` renvoie une redirection vers HTTPS | TASK-06, TASK-23 |
| SEC-I07 | T-I07 réponses bavardes | schémas de réponse explicites | `test_as03_bet_view_shows_only_pseudonyms_and_totals`, `test_as03_responses_never_contain_password_hash` | TASK-12, TASK-14 |
| SEC-I08 | T-I08 traversée de répertoire | `StaticFiles` sur un répertoire dédié | `test_as16_static_path_traversal_returns_404` (paramétré : `../`, `%2e%2e/`, `..%2f`) | TASK-22 |
| SEC-D01 | T-D01 inondation | limites d'écriture, interrupteurs | `test_as13_write_rate_limit_returns_429_with_retry_after`, `test_feature_switch_returns_503` | TASK-02, TASK-07 |
| SEC-D02 | T-D02 épuisement argon2 | limite avant hash, 128 caractères au plus | `test_as01_long_password_rejected_without_hashing`, `test_as01_rate_limit_applies_before_hashing` | TASK-09 |
| SEC-D03 | T-D03 requêtes énormes | 64 Ko, pagination bornée | `test_as13_oversized_body_returns_413`, `test_as13_page_size_capped_at_50` | TASK-06 |
| SEC-D04 | T-D04 contestations en masse | une contestation par participant ayant misé, seuil pondéré | `test_as10_contest_requires_stake`, `test_as10_second_contest_rejected`, `test_as10_small_stakes_do_not_reach_threshold` | TASK-18 |
| SEC-D05 | T-D05 PSP lent | timeouts, 503, écriture compensatoire | `test_us02_5_psp_timeout_marks_deposit_failed`, `test_us04_5_psp_failure_reverses_withdrawal` | TASK-10, TASK-11, TASK-13 |
| SEC-D06 | T-D06 fonds bloqués | escalade à 72 h | `test_as09_bet_without_result_escalates_after_72h` | TASK-20 |
| SEC-E01 | T-E01 auto-promotion | rôle jamais lu depuis la requête ou le jeton, CLI | `test_as12_role_field_rejected_on_register`, `test_as12_role_claim_in_token_grants_nothing` | TASK-09, TASK-21 |
| SEC-E02 | T-E02 contrôle d'accès fonctionnel | refus par défaut, dépendances par route | `test_as12_all_api_routes_require_auth_except_public_list`, `test_as12_non_moderator_gets_403_on_moderation_routes`, `test_as09_participant_cannot_propose_result` | TASK-09, TASK-17, TASK-21 |
| SEC-E03 | T-E03 lien d'invitation | jeton de 256 bits haché, usage unique atomique, 10 / min | `test_as06_used_invitation_cannot_be_reused`, `test_as06_expired_invitation_rejected`, `test_as06_invitation_accept_rate_limited`, `test_invitation_token_stored_hashed` | TASK-15 |
| SEC-E04 | T-E04 conflit d'intérêts | séparation des rôles | `test_as14_bookie_cannot_stake_or_join_own_bet`, `test_as14_moderator_cannot_stake_create_or_join` | TASK-15, TASK-16, TASK-21 |
| SEC-E05 | T-E05 SSRF | aucune requête vers une URL fournie par un utilisateur | `test_as09_evidence_url_never_fetched` (transport `httpx` espion) | TASK-17 |
| SEC-E06 | T-E06 dépendance vulnérable | `uv.lock` avec hash, bibliothèques maintenues, conteneur non root | `uv lock --check`, `test_forbidden_dependencies_absent` (ni `python-jose` ni `passlib`), `docker compose exec app id -u` différent de 0 | TASK-01, TASK-23 |

## Lot 4 : prévu aux ateliers suivants

Pas dans le prompt de génération du MVP, listé pour que le socle soit prêt à les accueillir.

- Atelier 2, Semgrep : règles maison proposées : modification de `balance_cents` hors de `services/money.py` ; `text()` ou `execute()` avec f-string ou concaténation ; route FastAPI sans dépendance d'authentification hors de la liste publique ; `innerHTML` dans `static/js`.
- Atelier 2, DAST et fuzzing : ZAP et ffuf sur `POST /api/bets` et `POST /api/bets/{id}/stakes`.
- Atelier 2, E2E et charge : Playwright sur le parcours connexion, dépôt, mise, déconnexion (jeton refusé ensuite) ; Locust avec 500 parieurs sur un même pari ; test de concurrence sur le solde.
- Atelier 3 : pipeline GitHub Actions (ruff, mypy, pytest sur SQLite et service PostgreSQL, Semgrep, gitleaks, SBOM CycloneDX).
- Atelier 4 : règles d'alerte sur les événements de `design.md` §12 ; expériences de chaos : PSP à 10 s de latence (dépôts en 503, mises sur solde intactes), base indisponible (503 générique, aucune écriture partielle), worker arrêté (mises toujours refusées après `closes_at`).
