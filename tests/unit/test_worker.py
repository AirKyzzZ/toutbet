import io
import json
from datetime import timedelta

from sqlalchemy import func, select

from support import DEFAULT_PASSWORD, Api, idempotency_headers, proposed_bet
from toutbet.models import LedgerEntry, Withdrawal
from toutbet.psp.client import PayoutStatus, PspUnavailableError
from toutbet.services.verification import verify_ledger
from toutbet.worker import emit_ledger_head


def test_tick_twice_is_harmless(api: Api) -> None:
    scenario = proposed_bet(api, [("alice", "OUI", 1000), ("bob", "NON", 3000)], outcome="OUI")
    api.end_contest_window(*scenario.everyone)
    balances = {name: api.balance(p) for name, p in scenario.players.items()}
    with api.db() as db:
        entries_before = db.scalar(select(func.count()).select_from(LedgerEntry))

    second = api.tick()

    assert second.closed == second.escalated == second.settled == 0
    assert {name: api.balance(p) for name, p in scenario.players.items()} == balances
    with api.db() as db:
        assert db.scalar(select(func.count()).select_from(LedgerEntry)) == entries_before
    assert api.bet(scenario.bookie, scenario.bet["id"])["status"] == "settled"


def test_worker_emits_ledger_head(api: Api, captured_logs: io.StringIO) -> None:
    player = api.signup()
    api.fund(player, 1000)
    with api.db() as db:
        head = db.scalars(select(LedgerEntry).order_by(LedgerEntry.seq.desc())).first()
    assert head is not None

    factory = api.client.app.state.session_factory
    with factory() as session:
        emit_ledger_head(session)

    events = [json.loads(line) for line in captured_logs.getvalue().splitlines()]
    published = [event for event in events if event["event"] == "ledger.head"]
    assert len(published) == 1
    assert published[0]["ledger_seq"] == head.seq
    assert published[0]["ledger_hash"] == head.hash
    assert published[0]["audit_seq"] == 0


def _pending_withdrawal(api: Api) -> str:
    player = api.signup()
    api.fund(player, 5000)
    api.verify_kyc(player)
    api.relogin(player)
    api.psp.payout_error = PspUnavailableError("ConnectTimeout")
    response = api.client.post(
        "/api/withdrawals",
        json={"amount_cents": 2000, "password": DEFAULT_PASSWORD},
        headers=idempotency_headers(player),
    )
    assert response.status_code == 503
    api.psp.payout_error = None
    with api.db() as db:
        withdrawal = db.scalars(select(Withdrawal)).one()
    assert withdrawal.status == "pending"
    return str(withdrawal.id)


def _withdrawal_and_ledger(api: Api) -> tuple[str, list[str]]:
    with api.db() as db:
        status = db.scalars(select(Withdrawal.status)).one()
        kinds = db.scalars(select(LedgerEntry.entry_type).order_by(LedgerEntry.seq)).all()
        assert verify_ledger(db).ok
    return status, list(kinds)


def test_worker_resubmits_unknown_payout_under_the_same_reference(api: Api) -> None:
    withdrawal_id = _pending_withdrawal(api)

    early = api.tick()
    api.clock.advance(timedelta(minutes=10))
    report = api.tick()
    api.clock.advance(timedelta(minutes=10))
    again = api.tick()

    assert early.reconciled == 0
    assert report.reconciled == 1
    assert again.reconciled == 0
    assert [payout["reference"] for payout in api.psp.payouts] == [withdrawal_id]
    assert _withdrawal_and_ledger(api) == ("sent", ["deposit", "withdrawal"])


def test_worker_reverses_withdrawal_refused_by_psp(api: Api) -> None:
    withdrawal_id = _pending_withdrawal(api)
    api.psp.payout_statuses[withdrawal_id] = PayoutStatus("po_refused", "refused")
    api.clock.advance(timedelta(minutes=10))

    report = api.tick()

    assert report.reconciled == 1
    assert api.psp.payouts == []
    assert _withdrawal_and_ledger(api) == (
        "failed",
        ["deposit", "withdrawal", "withdrawal_reversal"],
    )


def test_worker_keeps_withdrawal_pending_while_psp_unreachable(
    api: Api, captured_logs: io.StringIO
) -> None:
    _pending_withdrawal(api)
    api.psp.lookup_error = PspUnavailableError("ReadTimeout")
    api.clock.advance(timedelta(minutes=10))

    report = api.tick()

    assert report.reconciled == 0
    assert api.psp.payouts == []
    assert _withdrawal_and_ledger(api) == ("pending", ["deposit", "withdrawal"])
    events = [json.loads(line) for line in captured_logs.getvalue().splitlines()]
    assert [e["event"] for e in events].count("money.withdrawal_unconfirmed") == 2
