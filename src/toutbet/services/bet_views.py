import uuid

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from toutbet.models import Bet, Contest, ModerationDecision, Participant, Stake, User
from toutbet.schemas.bets import BetView, DecisionView, MyStake, OutcomeTotals
from toutbet.schemas.moderation import ContestView, DisputeView


def outcome_totals(db: Session, bet_id: uuid.UUID) -> OutcomeTotals:
    rows = db.execute(
        select(Stake.outcome, func.sum(Stake.amount_cents))
        .where(Stake.bet_id == bet_id)
        .group_by(Stake.outcome)
    )
    totals = {outcome: int(total) for outcome, total in rows}
    return OutcomeTotals(OUI=totals.get("OUI", 0), NON=totals.get("NON", 0))


def display_name(db: Session, user_id: uuid.UUID) -> str:
    return db.execute(select(User.display_name).where(User.id == user_id)).scalar_one()


def _participant_names(db: Session, bet_id: uuid.UUID) -> list[str]:
    return list(
        db.scalars(
            select(User.display_name)
            .join(Participant, Participant.user_id == User.id)
            .where(Participant.bet_id == bet_id)
            .order_by(User.display_name)
        )
    )


def _decision_view(db: Session, bet_id: uuid.UUID) -> DecisionView | None:
    decision = db.scalar(select(ModerationDecision).where(ModerationDecision.bet_id == bet_id))
    if decision is None:
        return None
    return DecisionView(
        moderator=display_name(db, decision.moderator_id),
        outcome=decision.outcome,
        justification=decision.justification,
        created_at=decision.created_at,
    )


def build_bet_view(db: Session, bet: Bet, viewer_id: uuid.UUID) -> BetView:
    stake = db.scalar(select(Stake).where(Stake.bet_id == bet.id, Stake.user_id == viewer_id))
    contested = db.scalar(
        select(Contest.id).where(Contest.bet_id == bet.id, Contest.user_id == viewer_id)
    )
    return BetView(
        id=bet.id,
        title=bet.title,
        description=bet.description,
        bookie=display_name(db, bet.bookie_id),
        my_role="bookie" if bet.bookie_id == viewer_id else "participant",
        status=bet.status,
        event_starts_at=bet.event_starts_at,
        closes_at=bet.closes_at,
        bookie_commission_bps=bet.bookie_commission_bps,
        platform_commission_bps=bet.platform_commission_bps,
        min_stake_cents=bet.min_stake_cents,
        totals=outcome_totals(db, bet.id),
        participants=_participant_names(db, bet.id),
        my_stake=None
        if stake is None
        else MyStake(
            id=stake.id,
            outcome=stake.outcome,
            amount_cents=stake.amount_cents,
            created_at=stake.created_at,
        ),
        my_contested=contested is not None,
        proposed_outcome=bet.proposed_outcome,
        proposal_note=bet.proposal_note,
        contest_deadline=bet.contest_deadline,
        final_outcome=bet.final_outcome,
        bookie_fee_forfeited=bet.bookie_fee_forfeited,
        settled_at=bet.settled_at,
        decision=_decision_view(db, bet.id),
    )


def build_dispute_view(db: Session, bet: Bet) -> DisputeView:
    contests = db.execute(
        select(User.display_name, Contest.reason, Contest.created_at)
        .join(User, User.id == Contest.user_id)
        .where(Contest.bet_id == bet.id)
        .order_by(Contest.created_at)
    )
    return DisputeView(
        id=bet.id,
        title=bet.title,
        description=bet.description,
        bookie=display_name(db, bet.bookie_id),
        status=bet.status,
        event_starts_at=bet.event_starts_at,
        proposed_outcome=bet.proposed_outcome,
        proposal_note=bet.proposal_note,
        totals=outcome_totals(db, bet.id),
        contests=[
            ContestView(contester=name, reason=reason, created_at=created_at)
            for name, reason, created_at in contests
        ],
    )
