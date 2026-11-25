import logging
import uuid
from datetime import datetime
from typing import Any, cast

from sqlalchemy import CursorResult, select, update
from sqlalchemy.orm import Session

from toutbet.errors import ApiError, ErrorCode
from toutbet.models import Deposit
from toutbet.models.payment import DEPOSIT_FAILED, DEPOSIT_PENDING, DEPOSIT_SUCCEEDED
from toutbet.observability import log_event
from toutbet.schemas.psp import PaymentEvent
from toutbet.security.authz import CurrentUser
from toutbet.services.ledger import Trace
from toutbet.services.money import PSP_CLEARING, transfer, wallet_account

CURRENCY = "EUR"


def open_deposit(db: Session, *, user: CurrentUser, amount_cents: int, now: datetime) -> Deposit:
    deposit = Deposit(
        id=uuid.uuid4(),
        user_id=user.id,
        amount_cents=amount_cents,
        currency=CURRENCY,
        status=DEPOSIT_PENDING,
        created_at=now,
    )
    db.add(deposit)
    db.flush()
    return deposit


def attach_checkout(db: Session, deposit_id: uuid.UUID, psp_session_id: str) -> None:
    db.execute(
        update(Deposit)
        .where(Deposit.id == deposit_id, Deposit.status == DEPOSIT_PENDING)
        .values(psp_session_id=psp_session_id)
        .execution_options(synchronize_session=False)
    )


def fail_deposit(db: Session, deposit_id: uuid.UUID) -> None:
    db.execute(
        update(Deposit)
        .where(Deposit.id == deposit_id, Deposit.status == DEPOSIT_PENDING)
        .values(status=DEPOSIT_FAILED)
        .execution_options(synchronize_session=False)
    )


def find_deposit_for(db: Session, user: CurrentUser, deposit_id: uuid.UUID) -> Deposit:
    deposit = db.get(Deposit, deposit_id)
    if deposit is not None and deposit.user_id == user.id:
        return deposit
    if deposit is not None:
        log_event("authz.denied", user_id=str(user.id), reason="deposit_not_owned")
    raise ApiError(404, ErrorCode.NOT_FOUND)


def credit_deposit(db: Session, event: PaymentEvent, now: datetime, request_id: str) -> None:
    deposit = db.execute(
        select(Deposit).where(Deposit.psp_session_id == event.data.session_id).with_for_update()
    ).scalar_one_or_none()
    if deposit is None or deposit.status != DEPOSIT_PENDING:
        log_event("psp.unmatched_payment", level=logging.WARNING, event_id=event.event_id)
        return
    if deposit.amount_cents != event.data.amount_cents or deposit.currency != event.data.currency:
        log_event(
            "psp.amount_mismatch",
            level=logging.ERROR,
            event_id=event.event_id,
            deposit_id=str(deposit.id),
            user_id=str(deposit.user_id),
        )
        return
    updated = cast(
        CursorResult[Any],
        db.execute(
            update(Deposit)
            .where(Deposit.id == deposit.id, Deposit.status == DEPOSIT_PENDING)
            .values(status=DEPOSIT_SUCCEEDED, psp_event_id=event.event_id)
            .execution_options(synchronize_session=False)
        ),
    )
    if updated.rowcount != 1:
        return
    transfer(
        db,
        entry_type="deposit",
        source=PSP_CLEARING,
        target=wallet_account(deposit.user_id),
        amount_cents=deposit.amount_cents,
        now=now,
        trace=Trace(request_id=request_id, idempotency_key=event.event_id),
    )
    log_event("money.deposit_credited", user_id=str(deposit.user_id), deposit_id=str(deposit.id))


def mark_payment_failed(db: Session, event: PaymentEvent) -> None:
    db.execute(
        update(Deposit)
        .where(Deposit.psp_session_id == event.data.session_id, Deposit.status == DEPOSIT_PENDING)
        .values(status=DEPOSIT_FAILED)
        .execution_options(synchronize_session=False)
    )
