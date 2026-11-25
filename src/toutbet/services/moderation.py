import uuid
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from toutbet.errors import ApiError, ErrorCode
from toutbet.models import Bet, ModerationDecision
from toutbet.models.bet import STATUS_DISPUTED
from toutbet.observability import log_event
from toutbet.security.authz import CurrentUser
from toutbet.services.bet_state import InvalidTransitionError
from toutbet.services.ledger import Trace, append_audit_event
from toutbet.services.settlement import apply_settlement


def list_disputes(db: Session, *, page: int, page_size: int) -> list[Bet]:
    query = (
        select(Bet)
        .where(Bet.status == STATUS_DISPUTED)
        .order_by(Bet.created_at, Bet.id)
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    return list(db.scalars(query))


def get_dispute(db: Session, bet_id: uuid.UUID, *, for_update: bool = False) -> Bet:
    bet = db.get(Bet, bet_id, with_for_update=True if for_update else None)
    if bet is None or bet.status != STATUS_DISPUTED:
        raise ApiError(404, ErrorCode.NOT_FOUND)
    return bet


def bookie_fee_is_forfeited(proposed_outcome: str | None, decided_outcome: str) -> bool:
    return proposed_outcome is None or proposed_outcome != decided_outcome


def decide(
    db: Session,
    *,
    moderator: CurrentUser,
    bet_id: uuid.UUID,
    outcome: str,
    justification: str,
    now: datetime,
    trace: Trace,
) -> Bet:
    bet = get_dispute(db, bet_id, for_update=True)
    forfeited = bookie_fee_is_forfeited(bet.proposed_outcome, outcome)
    db.add(
        ModerationDecision(
            id=uuid.uuid4(),
            bet_id=bet.id,
            moderator_id=moderator.id,
            outcome=outcome,
            justification=justification,
            created_at=now,
        )
    )
    try:
        db.flush()
        apply_settlement(
            db,
            bet=bet,
            expected_status=STATUS_DISPUTED,
            outcome=outcome,
            bookie_fee_forfeited=forfeited,
            now=now,
            trace=trace,
        )
    except (IntegrityError, InvalidTransitionError):
        raise ApiError(409, ErrorCode.INVALID_STATE) from None
    append_audit_event(
        db,
        event_type="moderation.decision",
        now=now,
        actor_id=moderator.id,
        bet_id=bet.id,
        payload={
            "outcome": outcome,
            "justification": justification,
            "bookie_fee_forfeited": forfeited,
        },
    )
    log_event(
        "moderation.decision",
        moderator_id=str(moderator.id),
        bet_id=str(bet.id),
        outcome=outcome,
        bookie_fee_forfeited=forfeited,
    )
    return bet
