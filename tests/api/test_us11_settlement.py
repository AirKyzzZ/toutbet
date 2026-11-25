import uuid

import pytest
from sqlalchemy import func, select

from support import Api, proposed_bet
from toutbet.models import Bet, LedgerEntry
from toutbet.services.bet_state import InvalidTransitionError
from toutbet.services.ledger import SYSTEM_TRACE
from toutbet.services.settlement import apply_settlement
from toutbet.services.verification import verify_ledger

WORKED_EXAMPLE = [
    ("alice", "OUI", 1000),
    ("bob", "OUI", 3000),
    ("chloe", "NON", 2000),
    ("david", "NON", 1500),
    ("emma", "NON", 700),
]


def _platform_revenue(api: Api, bet_id: str) -> int:
    with api.db() as db:
        total = db.scalar(
            select(func.sum(LedgerEntry.amount_cents)).where(
                LedgerEntry.to_account == "platform:revenue",
                LedgerEntry.bet_id == uuid.UUID(bet_id),
            )
        )
    return int(total or 0)


def test_us11_1_settlement_pays_shares_and_commissions_in_integers(api: Api) -> None:
    scenario = proposed_bet(api, WORKED_EXAMPLE, outcome="OUI", bookie_commission_bps=400)

    api.end_contest_window(*scenario.everyone)

    balances = {name: api.balance(player) for name, player in scenario.players.items()}
    assert balances == {"alice": 1956, "bob": 5866, "chloe": 0, "david": 0, "emma": 0}
    assert api.balance(scenario.bookie) == 168
    assert _platform_revenue(api, scenario.bet["id"]) == 210
    with api.db() as db:
        kinds = db.scalars(
            select(LedgerEntry.entry_type).where(
                LedgerEntry.bet_id == uuid.UUID(scenario.bet["id"]),
                LedgerEntry.entry_type != "stake",
            )
        ).all()
    assert sorted(kinds) == ["bookie_fee", "payout", "payout", "platform_fee"]


def test_us11_2_cancelled_or_one_sided_bets_refund_without_commission(api: Api) -> None:
    one_sided = proposed_bet(api, [("alice", "OUI", 1000), ("bob", "OUI", 600)], outcome="OUI")
    api.end_contest_window(*one_sided.everyone)

    assert api.balance(one_sided.players["alice"]) == 1000
    assert api.balance(one_sided.players["bob"]) == 600
    assert api.balance(one_sided.bookie) == 0
    assert _platform_revenue(api, one_sided.bet["id"]) == 0


def test_us11_2_annule_result_refunds_everyone(api: Api) -> None:
    scenario = proposed_bet(api, [("carol", "OUI", 1000), ("dan", "NON", 900)], outcome="ANNULE")

    api.end_contest_window(*scenario.everyone)

    assert api.bet(scenario.bookie, scenario.bet["id"])["status"] == "cancelled"
    assert api.balance(scenario.players["carol"]) == 1000
    assert api.balance(scenario.players["dan"]) == 900
    assert _platform_revenue(api, scenario.bet["id"]) == 0


def test_us11_3_credits_and_fees_equal_total_and_escrow_ends_at_zero(api: Api) -> None:
    scenario = proposed_bet(api, WORKED_EXAMPLE, outcome="NON", bookie_commission_bps=1000)

    api.end_contest_window(*scenario.everyone)

    escrow = f"escrow:{scenario.bet['id']}"
    with api.db() as db:
        credited = db.scalar(
            select(func.sum(LedgerEntry.amount_cents)).where(LedgerEntry.to_account == escrow)
        )
        released = db.scalar(
            select(func.sum(LedgerEntry.amount_cents)).where(LedgerEntry.from_account == escrow)
        )
        report = verify_ledger(db)
    assert credited == released == 8200
    assert report.ok, report.message


def test_us11_4_bet_is_settled_at_most_once(api: Api) -> None:
    scenario = proposed_bet(api, WORKED_EXAMPLE, outcome="OUI")
    api.end_contest_window(*scenario.everyone)
    with api.db() as db:
        before = db.scalar(select(func.count()).select_from(LedgerEntry))

    with api.db() as db:
        bet = db.get(Bet, uuid.UUID(scenario.bet["id"]))
        assert bet is not None
        with pytest.raises(InvalidTransitionError):
            apply_settlement(
                db,
                bet=bet,
                expected_status="result_proposed",
                outcome="OUI",
                bookie_fee_forfeited=False,
                now=api.clock.now(),
                trace=SYSTEM_TRACE,
            )

    with api.db() as db:
        assert db.scalar(select(func.count()).select_from(LedgerEntry)) == before
    assert api.balance(scenario.players["alice"]) == 1956
