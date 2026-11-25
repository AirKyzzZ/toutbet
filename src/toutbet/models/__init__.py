from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from toutbet.models.base import Base
from toutbet.models.bet import (
    Bet,
    Contest,
    Invitation,
    ModerationDecision,
    Participant,
    Stake,
)
from toutbet.models.idempotency import IdempotencyRecord
from toutbet.models.ledger import (
    AUDIT_CHAIN,
    GENESIS_HASH,
    LEDGER_CHAIN,
    AuditEvent,
    ChainHead,
    LedgerEntry,
)
from toutbet.models.payment import Deposit, PspEvent, Withdrawal
from toutbet.models.user import LoginSession, User
from toutbet.models.wallet import Wallet

__all__ = [
    "AuditEvent",
    "Base",
    "Bet",
    "ChainHead",
    "Contest",
    "Deposit",
    "IdempotencyRecord",
    "Invitation",
    "LedgerEntry",
    "LoginSession",
    "ModerationDecision",
    "Participant",
    "PspEvent",
    "Stake",
    "User",
    "Wallet",
    "Withdrawal",
    "create_schema",
]


def create_schema(engine: Engine) -> None:
    Base.metadata.create_all(engine)
    with Session(engine) as session, session.begin():
        existing = set(session.scalars(select(ChainHead.name)))
        for name in (LEDGER_CHAIN, AUDIT_CHAIN):
            if name not in existing:
                session.add(ChainHead(name=name, last_seq=0, last_hash=GENESIS_HASH))
