import uuid
from datetime import datetime
from typing import Any, cast

from sqlalchemy import CursorResult, select, update
from sqlalchemy.orm import Session

from toutbet.models import LedgerEntry, Wallet
from toutbet.services.ledger import Trace, append_ledger_entry

WALLET_PREFIX = "wallet:"
ESCROW_PREFIX = "escrow:"
PLATFORM_REVENUE = "platform:revenue"
PSP_CLEARING = "psp:clearing"


class InsufficientFundsError(Exception):
    pass


def wallet_account(user_id: uuid.UUID) -> str:
    return f"{WALLET_PREFIX}{user_id}"


def escrow_account(bet_id: uuid.UUID) -> str:
    return f"{ESCROW_PREFIX}{bet_id}"


def wallet_owner(account: str) -> uuid.UUID | None:
    if account.startswith(WALLET_PREFIX):
        return uuid.UUID(account.removeprefix(WALLET_PREFIX))
    return None


def open_wallet(db: Session, user_id: uuid.UUID) -> None:
    db.add(Wallet(user_id=user_id, balance_cents=0))


def balance_of(db: Session, user_id: uuid.UUID) -> int:
    balance = db.scalar(select(Wallet.balance_cents).where(Wallet.user_id == user_id))
    if balance is None:
        raise LookupError("wallet not found")
    return balance


def transfer(
    db: Session,
    *,
    entry_type: str,
    source: str,
    target: str,
    amount_cents: int,
    now: datetime,
    trace: Trace,
    bet_id: uuid.UUID | None = None,
) -> LedgerEntry:
    if type(amount_cents) is not int or amount_cents <= 0:
        raise ValueError("amount must be a positive integer number of cents")
    if source == target:
        raise ValueError("source and target accounts must differ")
    payer = wallet_owner(source)
    if payer is not None:
        _debit_wallet(db, payer, amount_cents)
    payee = wallet_owner(target)
    if payee is not None:
        _credit_wallet(db, payee, amount_cents)
    return append_ledger_entry(
        db,
        entry_type=entry_type,
        from_account=source,
        to_account=target,
        amount_cents=amount_cents,
        now=now,
        trace=trace,
        bet_id=bet_id,
    )


def _debit_wallet(db: Session, user_id: uuid.UUID, amount_cents: int) -> None:
    statement = (
        update(Wallet)
        .where(Wallet.user_id == user_id, Wallet.balance_cents >= amount_cents)
        .values(balance_cents=Wallet.balance_cents - amount_cents)
        .execution_options(synchronize_session=False)
    )
    result = cast(CursorResult[Any], db.execute(statement))
    if result.rowcount != 1:
        raise InsufficientFundsError


def _credit_wallet(db: Session, user_id: uuid.UUID, amount_cents: int) -> None:
    statement = (
        update(Wallet)
        .where(Wallet.user_id == user_id)
        .values(balance_cents=Wallet.balance_cents + amount_cents)
        .execution_options(synchronize_session=False)
    )
    result = cast(CursorResult[Any], db.execute(statement))
    if result.rowcount != 1:
        raise LookupError("wallet not found")
