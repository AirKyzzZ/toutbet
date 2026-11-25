import logging
import uuid
from datetime import datetime
from typing import Any, cast

from sqlalchemy import CursorResult, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from toutbet.models import PspEvent, User
from toutbet.models.user import KYC_PENDING, KYC_REJECTED, KYC_VERIFIED
from toutbet.observability import log_event
from toutbet.schemas.psp import KycEvent, PaymentEvent, WebhookEnvelope
from toutbet.services.deposits import credit_deposit, mark_payment_failed

KYC_STATUSES = {"kyc.verified": KYC_VERIFIED, "kyc.rejected": KYC_REJECTED}


def _record_once(db: Session, event_id: str, event_type: str, now: datetime) -> bool:
    if db.get(PspEvent, event_id) is not None:
        return False
    try:
        with db.begin_nested():
            db.add(PspEvent(event_id=event_id, event_type=event_type, received_at=now))
    except IntegrityError:
        return False
    return True


def process_webhook(db: Session, payload: Any, now: datetime, request_id: str) -> None:
    envelope = WebhookEnvelope.model_validate(payload)
    with db.begin():
        if not _record_once(db, envelope.event_id, envelope.type, now):
            log_event("psp.webhook_replayed", event_id=envelope.event_id)
            return
        if envelope.type == "payment.succeeded":
            credit_deposit(db, PaymentEvent.model_validate(payload), now, request_id)
        elif envelope.type == "payment.failed":
            mark_payment_failed(db, PaymentEvent.model_validate(payload))
        else:
            _apply_kyc(db, KycEvent.model_validate(payload))


def _apply_kyc(db: Session, event: KycEvent) -> None:
    try:
        user_id = uuid.UUID(event.data.reference)
    except ValueError:
        log_event("psp.unmatched_kyc", event_id=event.event_id)
        return
    updated = cast(
        CursorResult[Any],
        db.execute(
            update(User)
            .where(User.id == user_id, User.kyc_status == KYC_PENDING)
            .values(kyc_status=KYC_STATUSES[event.type])
            .execution_options(synchronize_session=False)
        ),
    )
    if updated.rowcount != 1:
        log_event(
            "psp.kyc_event_ignored",
            level=logging.WARNING,
            user_id=str(user_id),
            event_id=event.event_id,
            event_type=event.type,
        )
        return
    log_event("kyc.status_changed", user_id=str(user_id), status=KYC_STATUSES[event.type])
