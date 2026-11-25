import io
import json

from sqlalchemy import func, select

from support import Api, proposed_bet
from toutbet.models import LedgerEntry
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
