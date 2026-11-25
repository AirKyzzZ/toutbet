import hashlib
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from toutbet.canonical import canonical_json, iso_utc
from toutbet.models import AuditEvent, ChainHead, LedgerEntry
from toutbet.models.ledger import AUDIT_CHAIN, LEDGER_CHAIN


@dataclass(frozen=True)
class Trace:
    actor_id: uuid.UUID | None = None
    session_id: uuid.UUID | None = None
    request_id: str | None = None
    idempotency_key: str | None = None


SYSTEM_TRACE = Trace()


def _optional_str(value: object) -> str | None:
    return None if value is None else str(value)


def ledger_entry_fields(entry: LedgerEntry) -> dict[str, Any]:
    return {
        "seq": entry.seq,
        "created_at": iso_utc(entry.created_at),
        "entry_type": entry.entry_type,
        "from_account": entry.from_account,
        "to_account": entry.to_account,
        "amount_cents": entry.amount_cents,
        "bet_id": _optional_str(entry.bet_id),
        "actor_id": _optional_str(entry.actor_id),
        "session_id": _optional_str(entry.session_id),
        "request_id": entry.request_id,
        "idempotency_key": entry.idempotency_key,
        "prev_hash": entry.prev_hash,
    }


def audit_event_fields(event: AuditEvent) -> dict[str, Any]:
    return {
        "seq": event.seq,
        "created_at": iso_utc(event.created_at),
        "event_type": event.event_type,
        "actor_id": _optional_str(event.actor_id),
        "bet_id": _optional_str(event.bet_id),
        "payload": event.payload,
        "prev_hash": event.prev_hash,
    }


def chain_hash(prev_hash: str, fields: dict[str, Any]) -> str:
    return hashlib.sha256((prev_hash + canonical_json(fields)).encode("utf-8")).hexdigest()


def lock_chain_head(db: Session, name: str) -> ChainHead:
    return db.execute(
        select(ChainHead).where(ChainHead.name == name).with_for_update()
    ).scalar_one()


def append_ledger_entry(
    db: Session,
    *,
    entry_type: str,
    from_account: str,
    to_account: str,
    amount_cents: int,
    now: datetime,
    trace: Trace,
    bet_id: uuid.UUID | None = None,
) -> LedgerEntry:
    head = lock_chain_head(db, LEDGER_CHAIN)
    entry = LedgerEntry(
        seq=head.last_seq + 1,
        created_at=now,
        entry_type=entry_type,
        from_account=from_account,
        to_account=to_account,
        amount_cents=amount_cents,
        bet_id=bet_id,
        actor_id=trace.actor_id,
        session_id=trace.session_id,
        request_id=trace.request_id,
        idempotency_key=trace.idempotency_key,
        prev_hash=head.last_hash,
    )
    entry.hash = chain_hash(entry.prev_hash, ledger_entry_fields(entry))
    db.add(entry)
    head.last_seq = entry.seq
    head.last_hash = entry.hash
    db.flush()
    return entry


def append_audit_event(
    db: Session,
    *,
    event_type: str,
    now: datetime,
    actor_id: uuid.UUID | None,
    bet_id: uuid.UUID | None,
    payload: dict[str, Any],
) -> AuditEvent:
    head = lock_chain_head(db, AUDIT_CHAIN)
    event = AuditEvent(
        seq=head.last_seq + 1,
        created_at=now,
        event_type=event_type,
        actor_id=actor_id,
        bet_id=bet_id,
        payload=canonical_json(payload),
        prev_hash=head.last_hash,
    )
    event.hash = chain_hash(event.prev_hash, audit_event_fields(event))
    db.add(event)
    head.last_seq = event.seq
    head.last_hash = event.hash
    db.flush()
    return event
