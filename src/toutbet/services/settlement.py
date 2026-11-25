import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from toutbet.models import Bet, Stake
from toutbet.models.bet import STATUS_CANCELLED, STATUS_SETTLED
from toutbet.services.bet_state import transition
from toutbet.services.ledger import Trace
from toutbet.services.money import PLATFORM_REVENUE, escrow_account, transfer, wallet_account

BPS_DENOMINATOR = 10_000
CANCELLED_OUTCOME = "ANNULE"


class SettlementInvariantError(Exception):
    pass


@dataclass(frozen=True)
class StakeLine:
    stake_id: uuid.UUID
    user_id: uuid.UUID
    outcome: str
    amount_cents: int
    created_at: datetime


@dataclass(frozen=True)
class Credit:
    stake_id: uuid.UUID
    user_id: uuid.UUID
    amount_cents: int
    entry_type: str


@dataclass(frozen=True)
class SettlementPlan:
    credits: tuple[Credit, ...]
    platform_fee_cents: int
    bookie_fee_cents: int


def compute_settlement(
    stakes: Sequence[StakeLine],
    outcome: str,
    bookie_commission_bps: int,
    platform_commission_bps: int,
    bookie_fee_forfeited: bool,
) -> SettlementPlan:
    winners = [stake for stake in stakes if stake.outcome == outcome]
    winning_pool = sum(stake.amount_cents for stake in winners)
    losing_pool = sum(stake.amount_cents for stake in stakes) - winning_pool
    if outcome == CANCELLED_OUTCOME or winning_pool == 0 or losing_pool == 0:
        refunds = tuple(
            Credit(stake.stake_id, stake.user_id, stake.amount_cents, "refund") for stake in stakes
        )
        return _checked(SettlementPlan(refunds, 0, 0), stakes)

    platform_fee = losing_pool * platform_commission_bps // BPS_DENOMINATOR
    bookie_fee = (
        0 if bookie_fee_forfeited else losing_pool * bookie_commission_bps // BPS_DENOMINATOR
    )
    distributable = losing_pool - platform_fee - bookie_fee

    shares = {s.stake_id: distributable * s.amount_cents // winning_pool for s in winners}
    remainders = {s.stake_id: distributable * s.amount_cents % winning_pool for s in winners}
    leftover = distributable - sum(shares.values())
    by_priority = sorted(winners, key=lambda s: (-remainders[s.stake_id], s.created_at, s.stake_id))
    bonus = {stake.stake_id for stake in by_priority[:leftover]}

    credits = tuple(
        Credit(
            stake.stake_id,
            stake.user_id,
            stake.amount_cents + shares[stake.stake_id] + (1 if stake.stake_id in bonus else 0),
            "payout",
        )
        for stake in winners
    )
    return _checked(SettlementPlan(credits, platform_fee, bookie_fee), stakes)


def _checked(plan: SettlementPlan, stakes: Sequence[StakeLine]) -> SettlementPlan:
    paid = sum(credit.amount_cents for credit in plan.credits)
    total = sum(stake.amount_cents for stake in stakes)
    if paid + plan.platform_fee_cents + plan.bookie_fee_cents != total:
        raise SettlementInvariantError("settlement does not conserve money")
    if any(credit.amount_cents <= 0 for credit in plan.credits):
        raise SettlementInvariantError("credits must be positive")
    return plan


def load_stake_lines(db: Session, bet_id: uuid.UUID) -> list[StakeLine]:
    stakes = db.scalars(select(Stake).where(Stake.bet_id == bet_id))
    return [
        StakeLine(stake.id, stake.user_id, stake.outcome, stake.amount_cents, stake.created_at)
        for stake in stakes
    ]


def apply_settlement(
    db: Session,
    *,
    bet: Bet,
    expected_status: str,
    outcome: str,
    bookie_fee_forfeited: bool,
    now: datetime,
    trace: Trace,
) -> SettlementPlan:
    target = STATUS_CANCELLED if outcome == CANCELLED_OUTCOME else STATUS_SETTLED
    transition(
        db,
        bet.id,
        expected_status,
        target,
        final_outcome=outcome,
        bookie_fee_forfeited=bookie_fee_forfeited,
        settled_at=now,
    )
    plan = compute_settlement(
        load_stake_lines(db, bet.id),
        outcome,
        bet.bookie_commission_bps,
        bet.platform_commission_bps,
        bookie_fee_forfeited,
    )
    escrow = escrow_account(bet.id)
    for credit in plan.credits:
        transfer(
            db,
            entry_type=credit.entry_type,
            source=escrow,
            target=wallet_account(credit.user_id),
            amount_cents=credit.amount_cents,
            now=now,
            trace=trace,
            bet_id=bet.id,
        )
    if plan.bookie_fee_cents:
        transfer(
            db,
            entry_type="bookie_fee",
            source=escrow,
            target=wallet_account(bet.bookie_id),
            amount_cents=plan.bookie_fee_cents,
            now=now,
            trace=trace,
            bet_id=bet.id,
        )
    if plan.platform_fee_cents:
        transfer(
            db,
            entry_type="platform_fee",
            source=escrow,
            target=PLATFORM_REVENUE,
            amount_cents=plan.platform_fee_cents,
            now=now,
            trace=trace,
            bet_id=bet.id,
        )
    return plan
