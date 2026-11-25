import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta

from fastapi.routing import iter_route_contexts
from fastapi.testclient import TestClient
from hypothesis import given, settings
from hypothesis import strategies as st
from sqlalchemy import func, select

from support import Api, proposed_bet
from toutbet.models import LedgerEntry
from toutbet.services.settlement import StakeLine, compute_settlement
from toutbet.worker import tick

EPOCH = datetime(2026, 1, 1, tzinfo=UTC)


def test_as11_concurrent_settlement_pays_once(api: Api) -> None:
    scenario = proposed_bet(
        api, [("alice", "OUI", 1000), ("bob", "OUI", 3000), ("carol", "NON", 4200)], outcome="OUI"
    )
    api.clock.advance(timedelta(hours=24, seconds=1))
    factory = api.client.app.state.session_factory
    workers = 4
    barrier = threading.Barrier(workers)

    def run_tick(_: int) -> int:
        with factory() as session:
            barrier.wait()
            return tick(session, api.clock, api.psp).settled

    with ThreadPoolExecutor(max_workers=workers) as pool:
        settled = list(pool.map(run_tick, range(workers)))
    api.relogin(*scenario.everyone)

    assert sorted(settled) == [0] * (workers - 1) + [1]
    with api.db() as db:
        payouts = db.scalar(
            select(func.count())
            .select_from(LedgerEntry)
            .where(
                LedgerEntry.entry_type == "payout",
                LedgerEntry.bet_id == uuid.UUID(scenario.bet["id"]),
            )
        )
    assert payouts == 2
    assert api.balance(scenario.players["carol"]) == 0
    assert api.balance(scenario.players["alice"]) + api.balance(scenario.players["bob"]) == (
        8200 - 210 - 168
    )


def test_as11_no_http_route_triggers_settlement(api: Api, client: TestClient) -> None:
    paths = [context.path or "" for context in iter_route_contexts(client.app.routes)]
    scenario = proposed_bet(api, [("alice", "OUI", 1000), ("bob", "NON", 1000)], outcome="OUI")
    api.clock.advance(timedelta(hours=24, seconds=1))
    api.relogin(*scenario.everyone)
    bet_id = scenario.bet["id"]

    attempts = [
        client.post(f"/api/bets/{bet_id}/{action}", headers=scenario.bookie.headers).status_code
        for action in ("settle", "payout", "settlement", "close")
    ]

    assert not any(word in path for path in paths for word in ("settle", "payout"))
    assert all(code in {404, 405} for code in attempts)
    assert api.bet(scenario.bookie, bet_id)["status"] == "result_proposed"
    assert api.balance(scenario.players["alice"]) == 0


@settings(max_examples=300, deadline=None)
@given(
    amounts=st.lists(st.integers(min_value=100, max_value=50_000), min_size=2, max_size=30),
    split=st.integers(min_value=1, max_value=29),
    bookie_bps=st.integers(min_value=0, max_value=1000),
)
def test_as11_rounding_never_creates_or_destroys_money(
    amounts: list[int], split: int, bookie_bps: int
) -> None:
    pivot = min(split, len(amounts) - 1)
    stakes = [
        StakeLine(
            uuid.uuid4(),
            uuid.uuid4(),
            "OUI" if i < pivot else "NON",
            amount,
            EPOCH + timedelta(seconds=i),
        )
        for i, amount in enumerate(amounts)
    ]

    plan = compute_settlement(stakes, "OUI", bookie_bps, 500, bookie_fee_forfeited=False)

    winners = [stake for stake in stakes if stake.outcome == "OUI"]
    losing_pool = sum(stake.amount_cents for stake in stakes) - sum(
        stake.amount_cents for stake in winners
    )
    distributed = sum(c.amount_cents for c in plan.credits) - sum(s.amount_cents for s in winners)
    assert distributed + plan.platform_fee_cents + plan.bookie_fee_cents == losing_pool
    shares = [c.amount_cents for c in plan.credits]
    assert len(shares) == len(winners)
