# ToutBet'

ToutBet' is a mobile-first peer-to-peer betting platform built for the EPSI course "Sécurité pour les devs". A bookie creates a private yes/no bet ("Matis va-t-il réussir son créneau ?"), invites friends with a single-use link, the platform holds the stakes in escrow, closes the bet before the event, lets the bookie propose the result, opens a 24 h contest window, escalates disputes to a moderator and pays the winners. Money is integer cents only, every movement is written to an append-only, hash-chained ledger, and every stake gets an Ed25519-signed receipt.

This is a sandbox MVP: no real money, a mock payment service provider (PSP), mocked KYC. The specifications live in `specs/` (in French) and the generation prompt and review checklist in `docs/prompts.md`.

## Prerequisites

- [uv](https://docs.astral.sh/uv/) (it installs Python 3.13 itself)
- Docker with Compose v2, only for the containerised stack and the PostgreSQL test run

## Setup

```sh
uv sync --locked
cp .env.example .env
uv run toutbet-admin gen-secrets
```

Paste the four printed lines (`JWT_SECRET`, `RECEIPT_SIGNING_KEY`, `PSP_WEBHOOK_SECRET`, `PSP_API_KEY`) over the empty placeholders in `.env`. The application refuses to start while a secret is missing or shorter than 32 bytes, and the error names the variable without printing its value. `.env` is ignored by Git and by the Docker build context.

## Running locally

Each command in its own terminal, from the repository root:

```sh
uv run toutbet-admin init-db
uv run mock-psp
uv run uvicorn --factory toutbet.main:build_app --port 8000 --no-server-header --no-access-log
uv run toutbet-worker
```

Open http://localhost:8000. The mock PSP listens on http://localhost:8001: its payment page only has "Payer" and "Refuser" buttons and its KYC page a "Valider mon identité" button. They send signed webhooks back to the API, exactly like a real PSP would. With `ENV=dev` the OpenAPI documentation is served at `/docs`; in any other environment `/docs`, `/redoc` and `/openapi.json` return 404.

## Running with Docker Compose

Compose reads the same `.env`. Besides the four secrets above it needs two database passwords:

```sh
echo "POSTGRES_OWNER_PASSWORD=$(openssl rand -hex 32)" >> .env
echo "APP_DB_PASSWORD=$(openssl rand -hex 32)" >> .env
docker compose --profile dev up --build
```

Services: `proxy` (Caddy, TLS with its internal CA, HTTP to HTTPS redirect, 64 KB body limit), `app` and `worker` (same non-root, read-only image), `postgres` (no published port, internal network only), `init-db` (one-shot: creates the schema with the owner role, then applies `deploy/postgres/grants.sql`) and `mock-psp` (`dev` profile only). The app runs with `ENV=prod`.

Browse https://localhost after trusting Caddy's local CA, rather than disabling TLS checks:

```sh
docker compose cp proxy:/data/caddy/pki/authorities/local/root.crt caddy-root.crt
curl --cacert caddy-root.crt -I https://localhost/
```

If your Docker runtime cannot publish ports 80 and 443 (Colima, for example), add a local override that maps them to high ports, the stack is otherwise unchanged.

## Tests and quality checks

```sh
uv run pytest
uv run ruff check
uv run ruff format --check
uv run mypy src
```

The suite runs offline on a temporary SQLite file per test. To run the same suite on PostgreSQL, start a disposable server that has the restricted application role, then point `DATABASE_URL` at that role and `DATABASE_ADMIN_URL` at the owner (the owner creates and cleans the schema, the tests run as the application role, and `test_pg_app_role_cannot_update_ledger` is no longer skipped):

```sh
docker run -d --rm --name toutbet-pgtest \
  -e POSTGRES_DB=toutbet -e POSTGRES_USER=toutbet_owner -e POSTGRES_PASSWORD=owner-test-only \
  -e APP_DB_PASSWORD=app-test-only \
  -v "$PWD/deploy/postgres/create-app-role.sh:/docker-entrypoint-initdb.d/10-create-app-role.sh:ro" \
  -p 127.0.0.1:55432:5432 postgres:17
DATABASE_URL=postgresql+psycopg://toutbet_app:app-test-only@127.0.0.1:55432/toutbet \
DATABASE_ADMIN_URL=postgresql+psycopg://toutbet_owner:owner-test-only@127.0.0.1:55432/toutbet \
uv run pytest
docker stop toutbet-pgtest
```

## Administration

```sh
uv run toutbet-admin gen-secrets
uv run toutbet-admin init-db [--grants deploy/postgres/grants.sql]
uv run toutbet-admin create-moderator --email mod@example.org --display-name mod_alice --birth-date 1985-01-31
uv run toutbet-admin verify-ledger
```

`create-moderator` reads the password twice with `getpass`; there is no `--password` option and no HTTP route that creates or promotes a moderator. `verify-ledger` recomputes the ledger and audit chains, checks every wallet against the sum of its entries, every deposit credit against a succeeded deposit and every finished bet's escrow, and stops with a non-zero exit code on the first anomaly. In Compose: `docker compose exec app toutbet-admin verify-ledger`.

## Abuse stories

| Abuse story | Test file |
|---|---|
| AS-01 | `tests/abuse/test_as01_account_takeover.py` |
| AS-02 | `tests/abuse/test_as02_forged_webhook.py` |
| AS-03 | `tests/abuse/test_as03_wallet_idor.py` |
| AS-04 | `tests/abuse/test_as04_fraudulent_withdrawal.py` |
| AS-05 | `tests/abuse/test_as05_bet_tampering.py` |
| AS-06 | `tests/abuse/test_as06_private_bet_intrusion.py` |
| AS-07 | `tests/abuse/test_as07_stake_tampering.py` |
| AS-08 | `tests/abuse/test_as08_past_posting.py` |
| AS-09 | `tests/abuse/test_as09_false_result.py` |
| AS-10 | `tests/abuse/test_as10_contest_flood.py` |
| AS-11 | `tests/abuse/test_as11_double_settlement.py` |
| AS-12 | `tests/abuse/test_as12_privilege_escalation.py` |
| AS-13 | `tests/abuse/test_as13_event_flood.py` |
| AS-14 | `tests/abuse/test_as14_collusion.py` |
| AS-15 | `tests/abuse/test_as15_repudiation.py` |
| AS-16 | `tests/abuse/test_as16_information_leak.py` |

## Threats, security tickets and tests


| Threat | Ticket | Tests and checks |
|---|---|---|
| T-S01 | SEC-S01 | `abuse/test_as01_account_takeover.py::test_as01_login_rate_limited_per_ip`<br>`abuse/test_as01_account_takeover.py::test_as01_spoofed_forwarded_for_does_not_bypass_limit`<br>`abuse/test_as01_account_takeover.py::test_as01_account_locked_after_ten_failures` |
| T-S02 | SEC-S02 | `abuse/test_as01_account_takeover.py::test_as01_tampered_token_rejected` |
| T-S03 | SEC-S03 | `abuse/test_as02_forged_webhook.py::test_as02_unsigned_webhook_rejected`<br>`abuse/test_as02_forged_webhook.py::test_as02_bad_signature_rejected`<br>`abuse/test_as02_forged_webhook.py::test_as02_stale_timestamp_rejected` |
| T-S04 | SEC-S04 | `api/test_us01_auth.py::test_us01_5_token_rejected_after_logout`<br>`api/test_us01_auth.py::test_us01_6_old_token_rejected_after_refresh`<br>`abuse/test_as04_fraudulent_withdrawal.py::test_as04_withdrawal_requires_password` |
| T-S05 | SEC-S05 | `abuse/test_as04_fraudulent_withdrawal.py::test_as04_withdrawal_requires_verified_kyc`<br>`abuse/test_as04_fraudulent_withdrawal.py::test_as04_destination_field_rejected`<br>`abuse/test_as04_fraudulent_withdrawal.py::test_as04_kyc_status_cannot_be_self_assigned` |
| T-S06 | SEC-S06 | `abuse/test_as10_contest_flood.py::test_as10_registration_rate_limited_per_ip`<br>`abuse/test_as10_contest_flood.py::test_as10_email_case_variants_are_one_account` |
| T-T01 | SEC-T01 | `abuse/test_as07_stake_tampering.py::test_as07_stake_after_close_rejected_before_worker_runs`<br>`abuse/test_as07_stake_tampering.py::test_as07_stake_cannot_be_modified_or_deleted` |
| T-T02 | SEC-T02 | `abuse/test_as07_stake_tampering.py::test_as07_concurrent_stakes_never_overspend`<br>`abuse/test_as04_fraudulent_withdrawal.py::test_as04_concurrent_withdrawals_never_overspend` |
| T-T03 | SEC-T03 | `abuse/test_as07_stake_tampering.py::test_as07_replayed_stake_debits_once`<br>`abuse/test_as07_stake_tampering.py::test_as07_reused_key_with_other_body_rejected`<br>`abuse/test_as07_stake_tampering.py::test_as07_missing_idempotency_key_rejected`<br>`abuse/test_as02_forged_webhook.py::test_as02_replayed_webhook_credits_once` |
| T-T04 | SEC-T04 | `abuse/test_as05_bet_tampering.py::test_as05_commission_above_cap_rejected`<br>`abuse/test_as05_bet_tampering.py::test_as05_unknown_fields_rejected`<br>`abuse/test_as05_bet_tampering.py::test_as05_non_integer_amounts_rejected` |
| T-T05 | SEC-T05 | `abuse/test_as09_false_result.py::test_as09_result_cannot_be_proposed_twice`<br>`abuse/test_as09_false_result.py::test_as09_overturned_result_forfeits_bookie_fee` |
| T-T06 | SEC-T06 | `abuse/test_as08_past_posting.py::test_as08_closes_at_after_event_start_rejected`<br>`abuse/test_as08_past_posting.py::test_as08_stake_at_closes_at_rejected`<br>`abuse/test_as08_past_posting.py::test_as08_client_timestamp_rejected`<br>`abuse/test_as05_bet_tampering.py::test_as05_bet_cannot_be_modified` |
| T-T07 | SEC-T07 | `abuse/test_as16_information_leak.py::test_as16_sql_injection_in_filters_rejected` |
| T-T08 | SEC-T08 | `abuse/test_as15_repudiation.py::test_as15_verify_ledger_detects_tampering`<br>`security/test_pg_grants.py::test_pg_app_role_cannot_update_ledger` |
| T-T09 | SEC-T09 | `abuse/test_as05_bet_tampering.py::test_as05_html_title_returned_verbatim_with_csp`<br>`security/test_static_assets.py::test_js_has_no_dangerous_sinks`<br>`security/test_static_assets.py::test_html_has_no_inline_script_or_style` |
| T-T10 | SEC-T10 | `abuse/test_as11_double_settlement.py::test_as11_concurrent_settlement_pays_once`<br>`abuse/test_as11_double_settlement.py::test_as11_no_http_route_triggers_settlement`<br>`unit/test_settlement.py::test_settlement_conserves_money` |
| T-R01 | SEC-R01 | `abuse/test_as15_repudiation.py::test_as15_receipt_signature_verifies_with_public_key`<br>`abuse/test_as15_repudiation.py::test_as15_stake_entry_records_session_and_request` |
| T-R02 | SEC-R02 | `abuse/test_as09_false_result.py::test_as09_result_proposal_recorded_in_audit_chain` |
| T-R03 | SEC-R03 | `abuse/test_as14_collusion.py::test_as14_decision_requires_justification`<br>`abuse/test_as14_collusion.py::test_as14_decision_recorded_and_visible_to_participants` |
| T-R04 | SEC-R04 | `security/test_logging.py::test_logs_are_json_lines_with_request_id`<br>`security/test_logging.py::test_request_id_stored_in_ledger_entry`<br>`unit/test_worker.py::test_worker_emits_ledger_head` |
| T-I01 | SEC-I01 | `abuse/test_as03_wallet_idor.py::test_as03_other_user_deposit_returns_404`<br>`abuse/test_as03_wallet_idor.py::test_as03_object_access_matrix`<br>`abuse/test_as06_private_bet_intrusion.py::test_as06_non_participant_cannot_read_or_stake` |
| T-I02 | SEC-I02 | `abuse/test_as16_information_leak.py::test_as16_unhandled_error_returns_generic_body`<br>`abuse/test_as16_information_leak.py::test_as16_validation_error_does_not_echo_input`<br>`abuse/test_as16_information_leak.py::test_as16_docs_disabled_outside_dev` |
| T-I03 | SEC-I03 | `abuse/test_as16_information_leak.py::test_as16_logs_never_contain_secrets`<br>`unit/test_redaction.py::test_redaction_masks_card_numbers_and_jwts` |
| T-I04 | SEC-I04 | `abuse/test_as01_account_takeover.py::test_as01_login_error_identical_for_unknown_email`<br>`abuse/test_as01_account_takeover.py::test_as01_register_response_identical_for_existing_email` |
| T-I05 | SEC-I05 | `unit/test_settings.py::test_settings_refuse_missing_or_weak_secrets`<br>`git check-ignore .env`<br>review of `.env.example` (R1) |
| T-I06 | SEC-I06 | `security/test_headers.py::test_security_headers_on_api_and_static`<br>`curl -I http://localhost` |
| T-I07 | SEC-I07 | `abuse/test_as03_wallet_idor.py::test_as03_bet_view_shows_only_pseudonyms_and_totals`<br>`abuse/test_as03_wallet_idor.py::test_as03_responses_never_contain_password_hash` |
| T-I08 | SEC-I08 | `abuse/test_as16_information_leak.py::test_as16_static_path_traversal_returns_404` |
| T-D01 | SEC-D01 | `abuse/test_as13_event_flood.py::test_as13_write_rate_limit_returns_429_with_retry_after`<br>`security/test_feature_switches.py::test_feature_switch_returns_503` |
| T-D02 | SEC-D02 | `abuse/test_as01_account_takeover.py::test_as01_long_password_rejected_without_hashing`<br>`abuse/test_as01_account_takeover.py::test_as01_rate_limit_applies_before_hashing` |
| T-D03 | SEC-D03 | `abuse/test_as13_event_flood.py::test_as13_oversized_body_returns_413`<br>`abuse/test_as13_event_flood.py::test_as13_page_size_capped_at_50` |
| T-D04 | SEC-D04 | `abuse/test_as10_contest_flood.py::test_as10_contest_requires_stake`<br>`abuse/test_as10_contest_flood.py::test_as10_second_contest_rejected`<br>`abuse/test_as10_contest_flood.py::test_as10_small_stakes_do_not_reach_threshold` |
| T-D05 | SEC-D05 | `api/test_us02_deposits.py::test_us02_5_psp_timeout_marks_deposit_failed`<br>`api/test_us04_withdrawals.py::test_us04_5_psp_failure_reverses_withdrawal` |
| T-D06 | SEC-D06 | `abuse/test_as09_false_result.py::test_as09_bet_without_result_escalates_after_72h` |
| T-E01 | SEC-E01 | `abuse/test_as12_privilege_escalation.py::test_as12_role_field_rejected_on_register`<br>`abuse/test_as12_privilege_escalation.py::test_as12_role_claim_in_token_grants_nothing` |
| T-E02 | SEC-E02 | `abuse/test_as12_privilege_escalation.py::test_as12_all_api_routes_require_auth_except_public_list`<br>`abuse/test_as12_privilege_escalation.py::test_as12_non_moderator_gets_403_on_moderation_routes`<br>`abuse/test_as09_false_result.py::test_as09_participant_cannot_propose_result` |
| T-E03 | SEC-E03 | `abuse/test_as06_private_bet_intrusion.py::test_as06_used_invitation_cannot_be_reused`<br>`abuse/test_as06_private_bet_intrusion.py::test_as06_expired_invitation_rejected`<br>`abuse/test_as06_private_bet_intrusion.py::test_as06_invitation_accept_rate_limited`<br>`abuse/test_as06_private_bet_intrusion.py::test_invitation_token_stored_hashed` |
| T-E04 | SEC-E04 | `abuse/test_as14_collusion.py::test_as14_bookie_cannot_stake_or_join_own_bet`<br>`abuse/test_as14_collusion.py::test_as14_moderator_cannot_stake_create_or_join` |
| T-E05 | SEC-E05 | `abuse/test_as09_false_result.py::test_as09_evidence_url_never_fetched` |
| T-E06 | SEC-E06 | `uv lock --check`<br>`security/test_dependencies.py::test_forbidden_dependencies_absent`<br>`docker compose exec app id -u` |

## Deviations and decisions

Where the specs were silent, ambiguous or in conflict, the more restrictive option was implemented.

- **JWT time checks.** `jwt.decode` always gets `algorithms=["HS256"]`, the audience, the issuer and `options={"require": [...]}`, but `exp` and `iat` are verified right after against the injected `Clock` instead of PyJWT's system clock (rule 10, and tests move time). Tokens carrying any claim other than the seven listed are rejected.
- **Account lock.** "10 failures in 15 minutes" needs the start of the window, so `users` has an extra `failed_login_window_started_at` column. The counter is updated with one conditional `UPDATE ... RETURNING`.
- **Webhook replay.** An extra `psp_events` table (primary key `event_id`) makes every webhook type idempotent, not only payments. Webhook payload models forbid unknown fields like every other request model.
- **Display names** are unique without regard to case. Registration answers `202` even when only the display name is taken: a distinct answer would reveal whether the email exists. The cost is that such a user cannot log in and must register again.
- **Bookie fee forfeiture.** The fee is cancelled when the moderator overturns the proposal and also when the bet was escalated because the bookie never proposed a result.
- **One-sided or annulled bets.** `OUI`/`NON` with an empty side ends `settled` with full refunds; `ANNULE` ends `cancelled`. Cancelled bets get `final_outcome = ANNULE` and `settled_at`.
- **PSP failures.** A deposit whose checkout fails is marked `failed`; a withdrawal whose payout fails or times out is reversed by a `withdrawal_reversal` entry and marked `failed`. Both answer `503 PSP_UNAVAILABLE` and release the idempotency key so the client can retry with the same key. A PSP refusal (4xx) is handled like unavailability. Payouts carry the withdrawal id as reference so a real PSP can deduplicate them.
- **Withdrawal re-authentication.** A missing password answers `401` (AS-04), not `422`. The password is checked before the idempotency lookup and is excluded from the request fingerprint so it is never stored, even hashed.
- **Contests.** A participant without a stake, or the bookie, gets `403`; an outsider `404`. Contesting before a proposal is `409 INVALID_STATE`; contesting after the deadline, or once the bet is disputed or settled, is `409 CONTEST_WINDOW_CLOSED`.
- **Invitations.** Accepting while already a participant answers `409 INVALID_STATE` and does not consume the token. A bookie accepting an invitation to their own bet gets `403 BOOKIE_CANNOT_STAKE`.
- **KYC.** Starting KYC when already verified answers `409`. A signed `kyc.rejected` webhook can downgrade a status.
- **Pagination.** `page_size` above 50 is rejected with `422` instead of being silently capped.
- **Body limit.** A non-numeric `Content-Length` also gets `413`.
- **Rate limiting.** slowapi decorators are applied in router factories so each `create_app(settings)` gets its own limiter and its own limits. slowapi's `RATELIMIT_ENABLED` environment switch is overridden: limits cannot be turned off by environment. Unauthenticated limits key on the client IP, authenticated ones on the user id; "write" and "read" are shared buckets per user.
- **Errors.** The trusted-host rejection keeps Starlette's plain `400 Invalid host header` body. `HEAD` is not routed for API endpoints.
- **Logs.** The card-number pattern only matches standalone digit groups so ledger hashes published in `ledger.head` are never mangled. The audit event `bet.result_proposed` stores the SHA-256 of the bookie's note, while `moderation.decision` keeps the justification, which T-R03 requires.
- **Secrets.** `PSP_API_KEY` is also held to 32 bytes, and `gen-secrets` prints one so the app and the mock PSP can share it.
- **Database grants.** Besides `SELECT, INSERT, UPDATE` on business tables, the application role gets `DELETE` on `idempotency_keys` only (24 h purge and key release). `ledger_entries` and `audit_events` stay `SELECT, INSERT`.
- **Compose networking.** Both networks use fixed subnets (`10.89.0.0/24`, `10.89.1.0/24`) so `TRUSTED_PROXY_IPS` can name Caddy's address. Caddy also listens on an unpublished plain-HTTP `:8080` on the front network so the mock PSP delivers webhooks through the proxy without disabling TLS verification. Uvicorn runs with `--no-proxy-headers` so only the app's `ProxyHeadersMiddleware` reads `X-Forwarded-For`.
- **PostgreSQL tests.** The Compose database publishes no port, so the PostgreSQL test run uses a disposable container (above) instead of `docker compose up -d postgres`.
- **Mock PSP.** Its pages are served directly on http://localhost:8001 in dev and end with a link back to ToutBet' instead of a redirect, because `form-action 'self'` blocks cross-origin redirects after a form post.
- **Dependencies.** `uvicorn` is a runtime dependency (ASGI server and `ProxyHeadersMiddleware`). Emails are validated with a regular expression rather than adding `email-validator`. Starlette's test client warns that it prefers `httpx2`; the warning is filtered instead of adding a dependency.
