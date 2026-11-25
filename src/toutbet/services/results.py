import hashlib
import uuid
from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from toutbet.canonical import iso_utc
from toutbet.errors import ApiError, ErrorCode
from toutbet.models import Bet, Contest, Stake
from toutbet.models.bet import (
    STATUS_CLOSED,
    STATUS_DISPUTED,
    STATUS_OPEN,
    STATUS_RESULT_PROPOSED,
)
from toutbet.observability import log_event
from toutbet.security.authz import CurrentUser, deny
from toutbet.services.bet_state import transition, transition_or_conflict
from toutbet.services.bets import find_bet_for
from toutbet.services.ledger import append_audit_event

CONTEST_WINDOW = timedelta(hours=24)
DISPUTE_THRESHOLD_PERCENT = 10


def propose_result(
    db: Session,
    *,
    user: CurrentUser,
    bet_id: uuid.UUID,
    outcome: str,
    note: str | None,
    now: datetime,
) -> Bet:
    bet = find_bet_for(db, user, bet_id, for_update=True)
    if bet.bookie_id != user.id:
        deny(user.id, "not_bookie")
    if bet.status != STATUS_CLOSED or now < bet.event_starts_at:
        raise ApiError(409, ErrorCode.INVALID_STATE)
    deadline = now + CONTEST_WINDOW
    transition_or_conflict(
        db,
        bet.id,
        STATUS_CLOSED,
        STATUS_RESULT_PROPOSED,
        proposed_outcome=outcome,
        proposal_note=note,
        contest_deadline=deadline,
    )
    note_digest = None if note is None else hashlib.sha256(note.encode("utf-8")).hexdigest()
    append_audit_event(
        db,
        event_type="bet.result_proposed",
        now=now,
        actor_id=user.id,
        bet_id=bet.id,
        payload={
            "outcome": outcome,
            "note_sha256": note_digest,
            "contest_deadline": iso_utc(deadline),
        },
    )
    log_event("bet.result_proposed", user_id=str(user.id), bet_id=str(bet.id), outcome=outcome)
    return bet


def contest_result(
    db: Session, *, user: CurrentUser, bet_id: uuid.UUID, reason: str, now: datetime
) -> Bet:
    bet = find_bet_for(db, user, bet_id, for_update=True)
    stake = db.scalar(select(Stake).where(Stake.bet_id == bet.id, Stake.user_id == user.id))
    if stake is None:
        deny(user.id, "contest_without_stake")
    if bet.status in (STATUS_OPEN, STATUS_CLOSED):
        raise ApiError(409, ErrorCode.INVALID_STATE)
    window_open = (
        bet.status == STATUS_RESULT_PROPOSED
        and bet.contest_deadline is not None
        and now < bet.contest_deadline
    )
    if not window_open:
        raise ApiError(409, ErrorCode.CONTEST_WINDOW_CLOSED)
    existing = db.scalar(
        select(Contest.id).where(Contest.bet_id == bet.id, Contest.user_id == user.id)
    )
    if existing is not None:
        raise ApiError(409, ErrorCode.ALREADY_CONTESTED)
    db.add(Contest(id=uuid.uuid4(), bet_id=bet.id, user_id=user.id, reason=reason, created_at=now))
    try:
        db.flush()
    except IntegrityError:
        raise ApiError(409, ErrorCode.ALREADY_CONTESTED) from None
    _escalate_if_threshold_reached(db, bet, now)
    return bet


def _escalate_if_threshold_reached(db: Session, bet: Bet, now: datetime) -> None:
    contested = db.scalar(
        select(func.coalesce(func.sum(Stake.amount_cents), 0)).where(
            Stake.bet_id == bet.id,
            Stake.user_id.in_(select(Contest.user_id).where(Contest.bet_id == bet.id)),
        )
    )
    total = db.scalar(
        select(func.coalesce(func.sum(Stake.amount_cents), 0)).where(Stake.bet_id == bet.id)
    )
    if contested is None or total is None or contested * 100 < total * DISPUTE_THRESHOLD_PERCENT:
        return
    transition(db, bet.id, STATUS_RESULT_PROPOSED, STATUS_DISPUTED)
    append_audit_event(
        db,
        event_type="bet.disputed",
        now=now,
        actor_id=None,
        bet_id=bet.id,
        payload={"reason": "contest_threshold", "contested_cents": contested, "total_cents": total},
    )
    log_event("bet.disputed", bet_id=str(bet.id), reason="contest_threshold")
