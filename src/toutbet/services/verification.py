from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from toutbet.models import AuditEvent, Bet, ChainHead, Deposit, LedgerEntry, Wallet
from toutbet.models.bet import STATUS_CANCELLED, STATUS_SETTLED
from toutbet.models.ledger import AUDIT_CHAIN, GENESIS_HASH, LEDGER_CHAIN
from toutbet.models.payment import DEPOSIT_SUCCEEDED
from toutbet.services.ledger import audit_event_fields, chain_hash, ledger_entry_fields
from toutbet.services.money import ESCROW_PREFIX, WALLET_PREFIX, escrow_account, wallet_account


@dataclass(frozen=True)
class VerificationReport:
    ok: bool
    message: str


def verify_ledger(db: Session) -> VerificationReport:
    checks: list[Callable[[Session], str | None]] = [
        _check_ledger_chain,
        _check_audit_chain,
        _check_wallet_balances,
        _check_deposits,
        _check_escrows,
    ]
    for check in checks:
        problem = check(db)
        if problem is not None:
            return VerificationReport(ok=False, message=problem)
    return VerificationReport(ok=True, message="ledger and audit chains verified")


def _check_chain(
    label: str,
    rows: Iterable[Any],
    fields: Callable[[Any], dict[str, Any]],
    head: ChainHead,
) -> str | None:
    expected_seq, expected_prev = 1, GENESIS_HASH
    for row in rows:
        if row.seq != expected_seq:
            return f"{label} entry {row.seq}: sequence gap, expected {expected_seq}"
        if row.prev_hash != expected_prev:
            return f"{label} entry {row.seq}: previous hash does not match"
        if chain_hash(row.prev_hash, fields(row)) != row.hash:
            return f"{label} entry {row.seq}: hash mismatch, the entry was modified"
        expected_seq, expected_prev = row.seq + 1, row.hash
    if head.last_seq != expected_seq - 1 or head.last_hash != expected_prev:
        return f"{label} head does not match the last entry"
    return None


def _head(db: Session, name: str) -> ChainHead:
    return db.execute(select(ChainHead).where(ChainHead.name == name)).scalar_one()


def _check_ledger_chain(db: Session) -> str | None:
    rows = db.scalars(select(LedgerEntry).order_by(LedgerEntry.seq))
    return _check_chain("ledger", rows, ledger_entry_fields, _head(db, LEDGER_CHAIN))


def _check_audit_chain(db: Session) -> str | None:
    rows = db.scalars(select(AuditEvent).order_by(AuditEvent.seq))
    return _check_chain("audit", rows, audit_event_fields, _head(db, AUDIT_CHAIN))


def _account_balances(db: Session, prefix: str) -> dict[str, int]:
    balances: dict[str, int] = {}
    credits = db.execute(
        select(LedgerEntry.to_account, func.sum(LedgerEntry.amount_cents))
        .where(LedgerEntry.to_account.startswith(prefix))
        .group_by(LedgerEntry.to_account)
    )
    for account, total in credits:
        balances[account] = balances.get(account, 0) + int(total)
    debits = db.execute(
        select(LedgerEntry.from_account, func.sum(LedgerEntry.amount_cents))
        .where(LedgerEntry.from_account.startswith(prefix))
        .group_by(LedgerEntry.from_account)
    )
    for account, total in debits:
        balances[account] = balances.get(account, 0) - int(total)
    return balances


def _check_wallet_balances(db: Session) -> str | None:
    expected = _account_balances(db, WALLET_PREFIX)
    known = set()
    for wallet in db.scalars(select(Wallet)):
        account = wallet_account(wallet.user_id)
        known.add(account)
        if wallet.balance_cents != expected.get(account, 0):
            return (
                f"wallet {wallet.user_id}: balance {wallet.balance_cents} differs from "
                f"ledger total {expected.get(account, 0)}"
            )
    orphans = set(expected) - known
    if orphans:
        return f"ledger references unknown wallet {sorted(orphans)[0]}"
    return None


def _check_deposits(db: Session) -> str | None:
    entries = db.scalars(select(LedgerEntry).where(LedgerEntry.entry_type == "deposit"))
    seen = 0
    for entry in entries:
        seen += 1
        deposit = db.scalar(select(Deposit).where(Deposit.psp_event_id == entry.idempotency_key))
        if (
            deposit is None
            or deposit.status != DEPOSIT_SUCCEEDED
            or deposit.amount_cents != entry.amount_cents
            or entry.to_account != wallet_account(deposit.user_id)
        ):
            return f"ledger entry {entry.seq}: deposit credit without matching succeeded deposit"
    succeeded = db.scalar(
        select(func.count()).select_from(Deposit).where(Deposit.status == DEPOSIT_SUCCEEDED)
    )
    if succeeded != seen:
        return "succeeded deposits and deposit ledger entries differ"
    return None


def _check_escrows(db: Session) -> str | None:
    balances = _account_balances(db, ESCROW_PREFIX)
    for account, balance in balances.items():
        if balance < 0:
            return f"{account}: negative escrow {balance}"
    finished = db.scalars(select(Bet.id).where(Bet.status.in_([STATUS_SETTLED, STATUS_CANCELLED])))
    for bet_id in finished:
        balance = balances.get(escrow_account(bet_id), 0)
        if balance != 0:
            return f"bet {bet_id}: escrow is {balance} after settlement"
    return None
