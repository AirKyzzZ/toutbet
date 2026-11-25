import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from toutbet.models.base import Base

GENESIS_HASH = "0" * 64
LEDGER_CHAIN = "ledger"
AUDIT_CHAIN = "audit"


class LedgerEntry(Base):
    __tablename__ = "ledger_entries"
    __table_args__ = (CheckConstraint("amount_cents > 0", name="ck_ledger_amount_positive"),)

    seq: Mapped[int] = mapped_column(primary_key=True, autoincrement=False)
    created_at: Mapped[datetime]
    entry_type: Mapped[str] = mapped_column(String(32))
    from_account: Mapped[str] = mapped_column(String(64), index=True)
    to_account: Mapped[str] = mapped_column(String(64), index=True)
    amount_cents: Mapped[int]
    bet_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("bets.id"))
    actor_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    session_id: Mapped[uuid.UUID | None]
    request_id: Mapped[str | None] = mapped_column(String(64))
    idempotency_key: Mapped[str | None] = mapped_column(String(128))
    prev_hash: Mapped[str] = mapped_column(String(64), unique=True)
    hash: Mapped[str] = mapped_column(String(64), unique=True)


class AuditEvent(Base):
    __tablename__ = "audit_events"

    seq: Mapped[int] = mapped_column(primary_key=True, autoincrement=False)
    created_at: Mapped[datetime]
    event_type: Mapped[str] = mapped_column(String(64))
    actor_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    bet_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("bets.id"), index=True)
    payload: Mapped[str] = mapped_column(Text)
    prev_hash: Mapped[str] = mapped_column(String(64), unique=True)
    hash: Mapped[str] = mapped_column(String(64), unique=True)


class ChainHead(Base):
    __tablename__ = "chain_heads"

    name: Mapped[str] = mapped_column(String(16), primary_key=True)
    last_seq: Mapped[int]
    last_hash: Mapped[str] = mapped_column(String(64))
