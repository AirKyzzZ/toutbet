import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import NoReturn

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from toutbet.canonical import iso_utc
from toutbet.errors import ApiError, ErrorCode
from toutbet.models import Stake
from toutbet.models.bet import STATUS_OPEN
from toutbet.observability import log_event
from toutbet.security.authz import CurrentUser
from toutbet.security.receipts import ReceiptSigner, SignedReceipt
from toutbet.services.bets import find_bet_for
from toutbet.services.ledger import Trace
from toutbet.services.money import InsufficientFundsError, escrow_account, transfer, wallet_account


@dataclass(frozen=True)
class PlacedStake:
    stake: Stake
    receipt: SignedReceipt


def _reject(
    user: CurrentUser, bet_id: uuid.UUID, status: int, code: ErrorCode, message: str | None = None
) -> NoReturn:
    log_event("money.stake_rejected", user_id=str(user.id), bet_id=str(bet_id), reason=code.value)
    raise ApiError(status, code, message)


def place_stake(
    db: Session,
    *,
    user: CurrentUser,
    bet_id: uuid.UUID,
    outcome: str,
    amount_cents: int,
    now: datetime,
    trace: Trace,
    signer: ReceiptSigner,
) -> PlacedStake:
    bet = find_bet_for(db, user, bet_id, for_share=True)
    if bet.bookie_id == user.id:
        _reject(user, bet_id, 403, ErrorCode.BOOKIE_CANNOT_STAKE)
    if bet.status != STATUS_OPEN or now >= bet.closes_at:
        _reject(user, bet_id, 409, ErrorCode.BET_CLOSED)
    if bet.min_stake_cents is not None and amount_cents < bet.min_stake_cents:
        _reject(
            user,
            bet_id,
            422,
            ErrorCode.VALIDATION_ERROR,
            "La mise est inférieure à la mise minimum du pari.",
        )
    already = db.scalar(select(Stake.id).where(Stake.bet_id == bet.id, Stake.user_id == user.id))
    if already is not None:
        _reject(user, bet_id, 409, ErrorCode.ALREADY_STAKED)
    try:
        entry = transfer(
            db,
            entry_type="stake",
            source=wallet_account(user.id),
            target=escrow_account(bet.id),
            amount_cents=amount_cents,
            now=now,
            trace=trace,
            bet_id=bet.id,
        )
    except InsufficientFundsError:
        _reject(user, bet_id, 409, ErrorCode.INSUFFICIENT_FUNDS)
    stake = Stake(
        id=uuid.uuid4(),
        bet_id=bet.id,
        user_id=user.id,
        outcome=outcome,
        amount_cents=amount_cents,
        ledger_hash=entry.hash,
        created_at=now,
    )
    db.add(stake)
    try:
        db.flush()
    except IntegrityError:
        _reject(user, bet_id, 409, ErrorCode.ALREADY_STAKED)
    receipt = signer.sign(
        {
            "stake_id": str(stake.id),
            "bet_id": str(bet.id),
            "user_id": str(user.id),
            "outcome": outcome,
            "amount_cents": amount_cents,
            "created_at": iso_utc(now),
            "ledger_hash": entry.hash,
        }
    )
    log_event("money.stake_placed", user_id=str(user.id), bet_id=str(bet.id))
    return PlacedStake(stake=stake, receipt=receipt)
