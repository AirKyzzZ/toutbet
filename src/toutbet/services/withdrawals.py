import logging
import uuid
from datetime import datetime, timedelta
from typing import Any, cast

from sqlalchemy import CursorResult, select, update
from sqlalchemy.orm import Session

from toutbet.errors import ApiError, ErrorCode
from toutbet.models import User, Withdrawal
from toutbet.models.payment import WITHDRAWAL_FAILED, WITHDRAWAL_PENDING, WITHDRAWAL_SENT
from toutbet.observability import log_event
from toutbet.psp.client import Payout, PspClient, PspRefusedError, PspUnavailableError
from toutbet.security.authz import CurrentUser
from toutbet.security.passwords import verify_password
from toutbet.services.deposits import CURRENCY
from toutbet.services.ledger import SYSTEM_TRACE, Trace
from toutbet.services.money import (
    PSP_CLEARING,
    InsufficientFundsError,
    transfer,
    wallet_account,
)

RECONCILIATION_DELAY = timedelta(minutes=5)


def password_matches(db: Session, user_id: uuid.UUID, password: str | None) -> bool:
    with db.begin():
        password_hash = db.scalar(select(User.password_hash).where(User.id == user_id))
    if password is None or password_hash is None:
        return False
    return verify_password(password_hash, password)


def open_withdrawal(
    db: Session, *, user: CurrentUser, amount_cents: int, now: datetime, trace: Trace
) -> Withdrawal:
    withdrawal = Withdrawal(
        id=uuid.uuid4(),
        user_id=user.id,
        amount_cents=amount_cents,
        status=WITHDRAWAL_PENDING,
        created_at=now,
    )
    db.add(withdrawal)
    db.flush()
    try:
        transfer(
            db,
            entry_type="withdrawal",
            source=wallet_account(user.id),
            target=PSP_CLEARING,
            amount_cents=amount_cents,
            now=now,
            trace=trace,
        )
    except InsufficientFundsError:
        log_event("money.withdrawal_rejected", user_id=str(user.id), reason="insufficient_funds")
        raise ApiError(409, ErrorCode.INSUFFICIENT_FUNDS) from None
    return withdrawal


def mark_sent(db: Session, withdrawal_id: uuid.UUID, payout_id: str) -> None:
    db.execute(
        update(Withdrawal)
        .where(Withdrawal.id == withdrawal_id, Withdrawal.status == WITHDRAWAL_PENDING)
        .values(status=WITHDRAWAL_SENT, psp_payout_id=payout_id)
        .execution_options(synchronize_session=False)
    )


def reverse_withdrawal(
    db: Session, *, withdrawal: Withdrawal, now: datetime, trace: Trace, reason: str
) -> None:
    updated = cast(
        CursorResult[Any],
        db.execute(
            update(Withdrawal)
            .where(Withdrawal.id == withdrawal.id, Withdrawal.status == WITHDRAWAL_PENDING)
            .values(status=WITHDRAWAL_FAILED)
            .execution_options(synchronize_session=False)
        ),
    )
    if updated.rowcount != 1:
        return
    transfer(
        db,
        entry_type="withdrawal_reversal",
        source=PSP_CLEARING,
        target=wallet_account(withdrawal.user_id),
        amount_cents=withdrawal.amount_cents,
        now=now,
        trace=trace,
    )
    log_event(
        "money.withdrawal_failed",
        user_id=str(withdrawal.user_id),
        withdrawal_id=str(withdrawal.id),
        reason=reason,
    )


def _unconfirmed_withdrawals(db: Session, now: datetime) -> list[Withdrawal]:
    with db.begin():
        return list(
            db.scalars(
                select(Withdrawal).where(
                    Withdrawal.status == WITHDRAWAL_PENDING,
                    Withdrawal.created_at <= now - RECONCILIATION_DELAY,
                )
            )
        )


def _resolve_payout(psp: PspClient, withdrawal: Withdrawal) -> Payout:
    known = psp.find_payout(reference=str(withdrawal.id))
    if known is None:
        return psp.create_payout(
            reference=str(withdrawal.id),
            user_reference=str(withdrawal.user_id),
            amount_cents=withdrawal.amount_cents,
            currency=CURRENCY,
        )
    if known.status == "refused":
        raise PspRefusedError("payout refused")
    return Payout(known.payout_id)


def reconcile_withdrawals(db: Session, now: datetime, psp: PspClient) -> int:
    reconciled = 0
    for withdrawal in _unconfirmed_withdrawals(db, now):
        try:
            payout = _resolve_payout(psp, withdrawal)
        except PspRefusedError:
            with db.begin():
                reverse_withdrawal(
                    db, withdrawal=withdrawal, now=now, trace=SYSTEM_TRACE, reason="psp_refused"
                )
            outcome = WITHDRAWAL_FAILED
        except PspUnavailableError as error:
            log_event(
                "money.withdrawal_unconfirmed",
                level=logging.WARNING,
                withdrawal_id=str(withdrawal.id),
                cause=str(error),
            )
            continue
        else:
            with db.begin():
                mark_sent(db, withdrawal.id, payout.payout_id)
            outcome = WITHDRAWAL_SENT
        log_event("money.withdrawal_reconciled", withdrawal_id=str(withdrawal.id), status=outcome)
        reconciled += 1
    return reconciled
