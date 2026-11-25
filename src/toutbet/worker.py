import logging
import signal
import threading
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import ColumnElement, select
from sqlalchemy.orm import Session, sessionmaker

from toutbet.clock import Clock, SystemClock
from toutbet.config import load_database_settings
from toutbet.db import make_engine, make_session_factory
from toutbet.models import Bet, ChainHead
from toutbet.models.bet import STATUS_CLOSED, STATUS_DISPUTED, STATUS_OPEN, STATUS_RESULT_PROPOSED
from toutbet.models.ledger import AUDIT_CHAIN, LEDGER_CHAIN
from toutbet.observability import configure_logging, log_event
from toutbet.services.bet_state import InvalidTransitionError, transition
from toutbet.services.idempotency import purge_expired
from toutbet.services.ledger import SYSTEM_TRACE, append_audit_event
from toutbet.services.settlement import apply_settlement

TICK_INTERVAL_SECONDS = 30
LEDGER_HEAD_INTERVAL_SECONDS = 300
ESCALATION_DELAY = timedelta(hours=72)


@dataclass
class TickReport:
    closed: int = 0
    escalated: int = 0
    settled: int = 0


def tick(session: Session, clock: Clock) -> TickReport:
    now = clock.now()
    report = TickReport(
        closed=_close_due_bets(session, now),
        escalated=_escalate_silent_bets(session, now),
        settled=_settle_uncontested_bets(session, now),
    )
    with session.begin():
        purge_expired(session, now)
    return report


def _due(session: Session, *criteria: ColumnElement[bool]) -> list[uuid.UUID]:
    with session.begin():
        return list(session.scalars(select(Bet.id).where(*criteria)))


def _close_due_bets(session: Session, now: datetime) -> int:
    closed = 0
    for bet_id in _due(session, Bet.status == STATUS_OPEN, Bet.closes_at <= now):
        try:
            with session.begin():
                transition(session, bet_id, STATUS_OPEN, STATUS_CLOSED)
            closed += 1
        except InvalidTransitionError:
            continue
    return closed


def _escalate_silent_bets(session: Session, now: datetime) -> int:
    escalated = 0
    due = _due(session, Bet.status == STATUS_CLOSED, Bet.event_starts_at <= now - ESCALATION_DELAY)
    for bet_id in due:
        try:
            with session.begin():
                transition(session, bet_id, STATUS_CLOSED, STATUS_DISPUTED)
                append_audit_event(
                    session,
                    event_type="bet.disputed",
                    now=now,
                    actor_id=None,
                    bet_id=bet_id,
                    payload={"reason": "no_result_after_72h"},
                )
            log_event("bet.disputed", bet_id=str(bet_id), reason="no_result_after_72h")
            escalated += 1
        except InvalidTransitionError:
            continue
    return escalated


def _settle_uncontested_bets(session: Session, now: datetime) -> int:
    settled = 0
    due = _due(session, Bet.status == STATUS_RESULT_PROPOSED, Bet.contest_deadline <= now)
    for bet_id in due:
        try:
            with session.begin():
                bet = session.get(Bet, bet_id, with_for_update=True)
                if bet is None or bet.proposed_outcome is None:
                    continue
                apply_settlement(
                    session,
                    bet=bet,
                    expected_status=STATUS_RESULT_PROPOSED,
                    outcome=bet.proposed_outcome,
                    bookie_fee_forfeited=False,
                    now=now,
                    trace=SYSTEM_TRACE,
                )
                append_audit_event(
                    session,
                    event_type="bet.settled",
                    now=now,
                    actor_id=None,
                    bet_id=bet_id,
                    payload={"outcome": bet.proposed_outcome, "trigger": "contest_window_elapsed"},
                )
            log_event("bet.settled", bet_id=str(bet_id))
            settled += 1
        except InvalidTransitionError:
            continue
    return settled


def emit_ledger_head(session: Session) -> None:
    with session.begin():
        ledger = session.get(ChainHead, LEDGER_CHAIN)
        audit = session.get(ChainHead, AUDIT_CHAIN)
    if ledger is None or audit is None:
        log_event("ledger.head_missing", level=logging.ERROR)
        return
    log_event(
        "ledger.head",
        ledger_seq=ledger.last_seq,
        ledger_hash=ledger.last_hash,
        audit_seq=audit.last_seq,
        audit_hash=audit.last_hash,
    )


def run(factory: sessionmaker[Session], clock: Clock, stop: threading.Event) -> None:
    last_head: float | None = None
    while not stop.is_set():
        started = time.monotonic()
        with factory() as session:
            try:
                report = tick(session, clock)
                log_event("worker.tick", **report.__dict__)
                if last_head is None or started - last_head >= LEDGER_HEAD_INTERVAL_SECONDS:
                    emit_ledger_head(session)
                    last_head = started
            except Exception:
                log_event("worker.tick_failed", level=logging.ERROR, exc_info=True)
        stop.wait(TICK_INTERVAL_SECONDS)


def main() -> None:
    configure_logging()
    settings = load_database_settings()
    engine = make_engine(settings.database_url.get_secret_value())
    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    log_event("worker.started")
    run(make_session_factory(engine), SystemClock(), stop)
    engine.dispose()
