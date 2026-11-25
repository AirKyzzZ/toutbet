import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from toutbet.models.base import Base, uuid_pk

STATUS_OPEN = "open"
STATUS_CLOSED = "closed"
STATUS_RESULT_PROPOSED = "result_proposed"
STATUS_DISPUTED = "disputed"
STATUS_SETTLED = "settled"
STATUS_CANCELLED = "cancelled"
PLATFORM_COMMISSION_BPS = 500


class Bet(Base):
    __tablename__ = "bets"
    __table_args__ = (
        CheckConstraint("closes_at <= event_starts_at", name="ck_bets_closes_before_event"),
        CheckConstraint(
            "bookie_commission_bps >= 0 AND bookie_commission_bps <= 1000",
            name="ck_bets_bookie_commission",
        ),
        CheckConstraint("platform_commission_bps = 500", name="ck_bets_platform_commission"),
        CheckConstraint(
            "min_stake_cents IS NULL OR (min_stake_cents >= 100 AND min_stake_cents <= 50000)",
            name="ck_bets_min_stake",
        ),
        CheckConstraint(
            "status IN ('open', 'closed', 'result_proposed', 'disputed', 'settled', 'cancelled')",
            name="ck_bets_status",
        ),
        CheckConstraint(
            "proposed_outcome IS NULL OR proposed_outcome IN ('OUI', 'NON', 'ANNULE')",
            name="ck_bets_proposed_outcome",
        ),
        CheckConstraint(
            "final_outcome IS NULL OR final_outcome IN ('OUI', 'NON', 'ANNULE')",
            name="ck_bets_final_outcome",
        ),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    bookie_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), index=True)
    title: Mapped[str] = mapped_column(String(140))
    description: Mapped[str | None] = mapped_column(String(1000))
    min_stake_cents: Mapped[int | None]
    bookie_commission_bps: Mapped[int]
    platform_commission_bps: Mapped[int] = mapped_column(default=PLATFORM_COMMISSION_BPS)
    event_starts_at: Mapped[datetime]
    closes_at: Mapped[datetime]
    status: Mapped[str] = mapped_column(String(16), default=STATUS_OPEN, index=True)
    proposed_outcome: Mapped[str | None] = mapped_column(String(8))
    proposal_note: Mapped[str | None] = mapped_column(String(500))
    contest_deadline: Mapped[datetime | None]
    final_outcome: Mapped[str | None] = mapped_column(String(8))
    bookie_fee_forfeited: Mapped[bool] = mapped_column(default=False)
    settled_at: Mapped[datetime | None]
    created_at: Mapped[datetime]


class Invitation(Base):
    __tablename__ = "invitations"

    id: Mapped[uuid.UUID] = uuid_pk()
    bet_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("bets.id"), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    created_at: Mapped[datetime]
    expires_at: Mapped[datetime]
    used_at: Mapped[datetime | None]
    used_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))


class Participant(Base):
    __tablename__ = "participants"

    bet_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("bets.id"), primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), primary_key=True)
    joined_at: Mapped[datetime]


class Stake(Base):
    __tablename__ = "stakes"
    __table_args__ = (
        UniqueConstraint("bet_id", "user_id", name="uq_stakes_bet_user"),
        CheckConstraint("amount_cents > 0", name="ck_stakes_amount_positive"),
        CheckConstraint("outcome IN ('OUI', 'NON')", name="ck_stakes_outcome"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    bet_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("bets.id"), index=True)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    outcome: Mapped[str] = mapped_column(String(3))
    amount_cents: Mapped[int]
    ledger_hash: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime]


class Contest(Base):
    __tablename__ = "contests"
    __table_args__ = (UniqueConstraint("bet_id", "user_id", name="uq_contests_bet_user"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    bet_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("bets.id"), index=True)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    reason: Mapped[str] = mapped_column(String(500))
    created_at: Mapped[datetime]


class ModerationDecision(Base):
    __tablename__ = "moderation_decisions"
    __table_args__ = (
        CheckConstraint("outcome IN ('OUI', 'NON', 'ANNULE')", name="ck_decisions_outcome"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    bet_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("bets.id"), unique=True)
    moderator_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    outcome: Mapped[str] = mapped_column(String(8))
    justification: Mapped[str] = mapped_column(String(1000))
    created_at: Mapped[datetime]
