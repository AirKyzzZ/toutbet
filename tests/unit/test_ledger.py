import itertools
import uuid
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from toutbet.models import AuditEvent, LedgerEntry, Wallet
from toutbet.models.ledger import GENESIS_HASH
from toutbet.services.accounts import create_user
from toutbet.services.ledger import (
    SYSTEM_TRACE,
    append_audit_event,
    chain_hash,
    ledger_entry_fields,
)
from toutbet.services.money import PSP_CLEARING, transfer, wallet_account
from toutbet.services.verification import verify_ledger

NOW = datetime(2026, 11, 25, 12, 0, 0, 123456, tzinfo=UTC)


def _funded_wallets(db: Session, count: int = 2) -> list[uuid.UUID]:
    users = []
    with db.begin():
        for index in range(count):
            user = create_user(
                db,
                email=f"ledger{index}@example.org",
                display_name=f"ledger_{index}",
                password_hash="$argon2id$placeholder",
                birth_date=date(1990, 1, 1),
                role="user",
                now=NOW,
            )
            users.append(user.id)
            transfer(
                db,
                entry_type="deposit_test",
                source=PSP_CLEARING,
                target=wallet_account(user.id),
                amount_cents=1000 + index,
                now=NOW + timedelta(microseconds=index),
                trace=SYSTEM_TRACE,
            )
    return users


def test_chain_links_each_entry_to_previous(db_session: Session) -> None:
    alice, bob = _funded_wallets(db_session)
    with db_session.begin():
        transfer(
            db_session,
            entry_type="transfer_test",
            source=wallet_account(alice),
            target=wallet_account(bob),
            amount_cents=250,
            now=NOW,
            trace=SYSTEM_TRACE,
        )
    db_session.expunge_all()

    with db_session.begin():
        entries = db_session.scalars(select(LedgerEntry).order_by(LedgerEntry.seq)).all()
        assert [entry.seq for entry in entries] == [1, 2, 3]
        assert entries[0].prev_hash == GENESIS_HASH
        for previous, current in itertools.pairwise(entries):
            assert current.prev_hash == previous.hash
        for entry in entries:
            assert chain_hash(entry.prev_hash, ledger_entry_fields(entry)) == entry.hash
        assert verify_ledger(db_session).ok


def test_verify_ledger_detects_modified_entry(db_session: Session, owner_session: Session) -> None:
    _funded_wallets(db_session, count=3)
    with owner_session.begin():
        owner_session.execute(
            update(LedgerEntry).where(LedgerEntry.seq == 2).values(to_account=PSP_CLEARING)
        )

    with db_session.begin():
        report = verify_ledger(db_session)

    assert not report.ok
    assert report.message.startswith("ledger entry 2:")


def test_verify_ledger_detects_balance_mismatch(db_session: Session) -> None:
    alice, _ = _funded_wallets(db_session)
    with db_session.begin():
        db_session.execute(
            update(Wallet).where(Wallet.user_id == alice).values(balance_cents=99_999)
        )

    with db_session.begin():
        report = verify_ledger(db_session)

    assert not report.ok
    assert f"wallet {alice}" in report.message


def test_verify_ledger_detects_modified_audit_event(
    db_session: Session, owner_session: Session
) -> None:
    with db_session.begin():
        for outcome in ("OUI", "NON"):
            append_audit_event(
                db_session,
                event_type="bet.result_proposed",
                now=NOW,
                actor_id=None,
                bet_id=None,
                payload={"outcome": outcome},
            )
    with owner_session.begin():
        owner_session.execute(
            update(AuditEvent).where(AuditEvent.seq == 1).values(payload='{"outcome":"NON"}')
        )

    with db_session.begin():
        report = verify_ledger(db_session)

    assert not report.ok
    assert report.message.startswith("audit entry 1:")


def test_verify_ledger_detects_deposit_credit_without_psp_payment(db_session: Session) -> None:
    alice, _ = _funded_wallets(db_session)
    with db_session.begin():
        transfer(
            db_session,
            entry_type="deposit",
            source=PSP_CLEARING,
            target=wallet_account(alice),
            amount_cents=5000,
            now=NOW,
            trace=SYSTEM_TRACE,
        )

    with db_session.begin():
        report = verify_ledger(db_session)

    assert not report.ok
    assert "deposit" in report.message
