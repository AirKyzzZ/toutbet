import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from toutbet.models.base import Base, uuid_pk

DEPOSIT_PENDING = "pending"
DEPOSIT_SUCCEEDED = "succeeded"
DEPOSIT_FAILED = "failed"
WITHDRAWAL_PENDING = "pending"
WITHDRAWAL_SENT = "sent"
WITHDRAWAL_FAILED = "failed"


class Deposit(Base):
    __tablename__ = "deposits"
    __table_args__ = (
        CheckConstraint("amount_cents > 0", name="ck_deposits_amount_positive"),
        CheckConstraint("status IN ('pending', 'succeeded', 'failed')", name="ck_deposits_status"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), index=True)
    amount_cents: Mapped[int]
    currency: Mapped[str] = mapped_column(String(3), default="EUR")
    status: Mapped[str] = mapped_column(String(16), default=DEPOSIT_PENDING)
    psp_session_id: Mapped[str | None] = mapped_column(String(128), unique=True)
    psp_event_id: Mapped[str | None] = mapped_column(String(128), unique=True)
    created_at: Mapped[datetime]


class Withdrawal(Base):
    __tablename__ = "withdrawals"
    __table_args__ = (
        CheckConstraint("amount_cents > 0", name="ck_withdrawals_amount_positive"),
        CheckConstraint("status IN ('pending', 'sent', 'failed')", name="ck_withdrawals_status"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), index=True)
    amount_cents: Mapped[int]
    status: Mapped[str] = mapped_column(String(16), default=WITHDRAWAL_PENDING)
    psp_payout_id: Mapped[str | None] = mapped_column(String(128), unique=True)
    created_at: Mapped[datetime]


class PspEvent(Base):
    __tablename__ = "psp_events"

    event_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    event_type: Mapped[str] = mapped_column(String(32))
    received_at: Mapped[datetime]
