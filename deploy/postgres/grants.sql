REVOKE ALL ON ALL TABLES IN SCHEMA public FROM PUBLIC;
REVOKE ALL ON ALL TABLES IN SCHEMA public FROM toutbet_app;
GRANT USAGE ON SCHEMA public TO toutbet_app;
GRANT SELECT, INSERT, UPDATE ON
    users,
    sessions,
    wallets,
    deposits,
    withdrawals,
    psp_events,
    bets,
    invitations,
    participants,
    stakes,
    contests,
    moderation_decisions,
    chain_heads,
    idempotency_keys
TO toutbet_app;
GRANT DELETE ON idempotency_keys TO toutbet_app;
GRANT SELECT, INSERT ON ledger_entries, audit_events TO toutbet_app;
