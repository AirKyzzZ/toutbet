import uuid
from datetime import UTC, datetime, timedelta
from typing import NoReturn

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from toutbet.errors import ApiError, ErrorCode
from toutbet.models import Bet, Participant
from toutbet.models.bet import PLATFORM_COMMISSION_BPS, STATUS_OPEN
from toutbet.observability import log_event
from toutbet.schemas.bets import BetCreate
from toutbet.security.authz import CurrentUser, deny
from toutbet.services.bet_state import InvalidTransitionError
from toutbet.services.ledger import Trace, append_audit_event
from toutbet.services.settlement import CANCELLED_OUTCOME, apply_settlement

MIN_CLOSING_LEAD = timedelta(minutes=5)
MAX_EVENT_HORIZON = timedelta(days=90)


def not_found() -> NoReturn:
    raise ApiError(404, ErrorCode.NOT_FOUND)


def is_participant(db: Session, bet_id: uuid.UUID, user_id: uuid.UUID) -> bool:
    found = db.scalar(
        select(Participant.user_id).where(
            Participant.bet_id == bet_id, Participant.user_id == user_id
        )
    )
    return found is not None


def find_bet_for(
    db: Session,
    user: CurrentUser,
    bet_id: uuid.UUID,
    *,
    for_update: bool = False,
    for_share: bool = False,
) -> Bet:
    lock: dict[str, bool] | None = None
    if for_update or for_share:
        lock = {"read": not for_update}
    bet = db.get(Bet, bet_id, with_for_update=lock)
    if bet is None:
        not_found()
    if bet.bookie_id == user.id or is_participant(db, bet.id, user.id):
        return bet
    log_event("authz.denied", user_id=str(user.id), reason="bet_not_visible", bet_id=str(bet_id))
    not_found()


def _invalid(message: str) -> NoReturn:
    raise ApiError(422, ErrorCode.VALIDATION_ERROR, message)


def create_bet(db: Session, *, user: CurrentUser, data: BetCreate, now: datetime) -> Bet:
    closes_at = data.closes_at.astimezone(UTC)
    event_starts_at = data.event_starts_at.astimezone(UTC)
    if closes_at - now < MIN_CLOSING_LEAD:
        _invalid("L'échéance doit être au moins 5 minutes dans le futur.")
    if closes_at > event_starts_at:
        _invalid("L'échéance doit précéder le début de l'événement.")
    if event_starts_at - now > MAX_EVENT_HORIZON:
        _invalid("L'événement doit avoir lieu dans les 90 jours.")
    bet = Bet(
        id=uuid.uuid4(),
        bookie_id=user.id,
        title=data.title,
        description=data.description,
        min_stake_cents=data.min_stake_cents,
        bookie_commission_bps=data.bookie_commission_bps,
        platform_commission_bps=PLATFORM_COMMISSION_BPS,
        event_starts_at=event_starts_at,
        closes_at=closes_at,
        status=STATUS_OPEN,
        bookie_fee_forfeited=False,
        created_at=now,
    )
    db.add(bet)
    db.flush()
    append_audit_event(
        db,
        event_type="bet.created",
        now=now,
        actor_id=user.id,
        bet_id=bet.id,
        payload={
            "closes_at": closes_at.isoformat(),
            "event_starts_at": event_starts_at.isoformat(),
            "bookie_commission_bps": bet.bookie_commission_bps,
            "min_stake_cents": bet.min_stake_cents,
        },
    )
    return bet


def list_bets(
    db: Session,
    *,
    user: CurrentUser,
    status: str | None,
    role: str | None,
    page: int,
    page_size: int,
) -> list[Bet]:
    joined = select(Participant.bet_id).where(Participant.user_id == user.id)
    query = select(Bet)
    if role == "bookie":
        query = query.where(Bet.bookie_id == user.id)
    elif role == "participant":
        query = query.where(Bet.id.in_(joined))
    else:
        query = query.where(or_(Bet.bookie_id == user.id, Bet.id.in_(joined)))
    if status is not None:
        query = query.where(Bet.status == status)
    query = query.order_by(Bet.created_at.desc(), Bet.id).offset((page - 1) * page_size)
    return list(db.scalars(query.limit(page_size)))


def cancel_bet(
    db: Session, *, user: CurrentUser, bet_id: uuid.UUID, now: datetime, trace: Trace
) -> Bet:
    bet = find_bet_for(db, user, bet_id, for_update=True)
    if bet.bookie_id != user.id:
        deny(user.id, "not_bookie")
    try:
        apply_settlement(
            db,
            bet=bet,
            expected_status=STATUS_OPEN,
            outcome=CANCELLED_OUTCOME,
            bookie_fee_forfeited=False,
            now=now,
            trace=trace,
        )
    except InvalidTransitionError:
        raise ApiError(409, ErrorCode.INVALID_STATE) from None
    append_audit_event(
        db, event_type="bet.cancelled", now=now, actor_id=user.id, bet_id=bet.id, payload={}
    )
    return bet
