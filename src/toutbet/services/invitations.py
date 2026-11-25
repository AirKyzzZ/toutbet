import hashlib
import secrets
import uuid
from datetime import datetime, timedelta
from typing import Any, cast

from sqlalchemy import CursorResult, func, select, update
from sqlalchemy.orm import Session

from toutbet.errors import ApiError, ErrorCode
from toutbet.models import Bet, Invitation, Participant
from toutbet.models.bet import STATUS_OPEN
from toutbet.observability import log_event
from toutbet.security.authz import CurrentUser, deny
from toutbet.services.bets import find_bet_for, is_participant, not_found

INVITATION_TTL = timedelta(hours=48)
MAX_ACTIVE_INVITATIONS = 50
TOKEN_BYTES = 32


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("ascii")).hexdigest()


def create_invitation(
    db: Session, *, user: CurrentUser, bet_id: uuid.UUID, now: datetime, public_base_url: str
) -> tuple[str, datetime]:
    bet = find_bet_for(db, user, bet_id, for_update=True)
    if bet.bookie_id != user.id:
        deny(user.id, "not_bookie")
    if bet.status != STATUS_OPEN or now >= bet.closes_at:
        raise ApiError(409, ErrorCode.INVALID_STATE)
    active = db.scalar(
        select(func.count())
        .select_from(Invitation)
        .where(
            Invitation.bet_id == bet.id,
            Invitation.used_at.is_(None),
            Invitation.expires_at > now,
        )
    )
    if active is not None and active >= MAX_ACTIVE_INVITATIONS:
        raise ApiError(409, ErrorCode.INVITATION_LIMIT)
    token = secrets.token_urlsafe(TOKEN_BYTES)
    expires_at = now + INVITATION_TTL
    db.add(
        Invitation(
            id=uuid.uuid4(),
            bet_id=bet.id,
            token_hash=hash_token(token),
            created_at=now,
            expires_at=expires_at,
        )
    )
    db.flush()
    return f"{public_base_url}/#/invite/{token}", expires_at


def accept_invitation(db: Session, *, user: CurrentUser, token: str, now: datetime) -> uuid.UUID:
    invitation = db.scalar(select(Invitation).where(Invitation.token_hash == hash_token(token)))
    if invitation is None:
        not_found()
    bet = db.get(Bet, invitation.bet_id)
    if bet is None:
        not_found()
    if bet.bookie_id == user.id:
        log_event("authz.denied", user_id=str(user.id), reason="bookie_joining_own_bet")
        raise ApiError(403, ErrorCode.BOOKIE_CANNOT_STAKE)
    usable = (
        invitation.used_at is None
        and invitation.expires_at > now
        and bet.status == STATUS_OPEN
        and now < bet.closes_at
    )
    if not usable:
        log_event("authz.denied", user_id=str(user.id), reason="invitation_unusable")
        not_found()
    if is_participant(db, bet.id, user.id):
        raise ApiError(409, ErrorCode.INVALID_STATE, "Vous participez déjà à ce pari.")
    consumed = cast(
        CursorResult[Any],
        db.execute(
            update(Invitation)
            .where(
                Invitation.id == invitation.id,
                Invitation.used_at.is_(None),
                Invitation.expires_at > now,
            )
            .values(used_at=now, used_by=user.id)
            .execution_options(synchronize_session=False)
        ),
    )
    if consumed.rowcount != 1:
        not_found()
    db.add(Participant(bet_id=bet.id, user_id=user.id, joined_at=now))
    db.flush()
    log_event("bet.joined", user_id=str(user.id), bet_id=str(bet.id))
    return bet.id
