import uuid
from datetime import UTC, datetime, timedelta

from hypothesis import given, settings
from hypothesis import strategies as st

from toutbet.services.settlement import StakeLine, compute_settlement

START = datetime(2026, 11, 25, 18, 0, tzinfo=UTC)


def _line(order: int, outcome: str, amount: int) -> StakeLine:
    return StakeLine(
        stake_id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        outcome=outcome,
        amount_cents=amount,
        created_at=START + timedelta(seconds=order),
    )


def _worked_example() -> list[StakeLine]:
    return [
        _line(1, "OUI", 1000),
        _line(2, "OUI", 3000),
        _line(3, "NON", 2000),
        _line(4, "NON", 1500),
        _line(5, "NON", 700),
    ]


def test_settlement_worked_example() -> None:
    stakes = _worked_example()

    plan = compute_settlement(stakes, "OUI", 400, 500, bookie_fee_forfeited=False)

    credits = {credit.stake_id: credit.amount_cents for credit in plan.credits}
    assert plan.platform_fee_cents == 210
    assert plan.bookie_fee_cents == 168
    assert credits == {stakes[0].stake_id: 1956, stakes[1].stake_id: 5866}
    assert sum(credits.values()) + 210 + 168 == 8200
    assert {credit.entry_type for credit in plan.credits} == {"payout"}


def test_settlement_worked_example_with_forfeited_bookie_fee() -> None:
    stakes = _worked_example()

    plan = compute_settlement(stakes, "OUI", 400, 500, bookie_fee_forfeited=True)

    credits = {credit.stake_id: credit.amount_cents for credit in plan.credits}
    assert plan.platform_fee_cents == 210
    assert plan.bookie_fee_cents == 0
    assert credits == {stakes[0].stake_id: 1998, stakes[1].stake_id: 5992}
    assert sum(credits.values()) + 210 == 8200


stake_lists = st.lists(
    st.tuples(st.sampled_from(["OUI", "NON"]), st.integers(min_value=100, max_value=50_000)),
    min_size=0,
    max_size=40,
)


@settings(max_examples=400, deadline=None)
@given(
    entries=stake_lists,
    outcome=st.sampled_from(["OUI", "NON", "ANNULE"]),
    bookie_bps=st.integers(min_value=0, max_value=1000),
    forfeited=st.booleans(),
)
def test_settlement_conserves_money(
    entries: list[tuple[str, int]], outcome: str, bookie_bps: int, forfeited: bool
) -> None:
    stakes = [_line(order, side, amount) for order, (side, amount) in enumerate(entries)]

    plan = compute_settlement(stakes, outcome, bookie_bps, 500, forfeited)

    total = sum(stake.amount_cents for stake in stakes)
    paid = sum(credit.amount_cents for credit in plan.credits)
    assert paid + plan.platform_fee_cents + plan.bookie_fee_cents == total
    assert all(isinstance(credit.amount_cents, int) for credit in plan.credits)
    by_stake = {stake.stake_id: stake for stake in stakes}
    for credit in plan.credits:
        assert credit.amount_cents >= by_stake[credit.stake_id].amount_cents
    if forfeited:
        assert plan.bookie_fee_cents == 0


def test_refund_when_one_side_is_empty() -> None:
    stakes = [_line(1, "OUI", 1000), _line(2, "OUI", 2500)]

    for outcome in ("OUI", "NON", "ANNULE"):
        plan = compute_settlement(stakes, outcome, 1000, 500, bookie_fee_forfeited=False)
        assert plan.platform_fee_cents == plan.bookie_fee_cents == 0
        assert sorted((c.amount_cents, c.entry_type) for c in plan.credits) == [
            (1000, "refund"),
            (2500, "refund"),
        ]


def test_leftover_cent_goes_to_oldest_stake_then_smallest_id() -> None:
    first = StakeLine(uuid.UUID(int=9), uuid.uuid4(), "OUI", 1000, START)
    second = StakeLine(uuid.UUID(int=1), uuid.uuid4(), "OUI", 1000, START)
    loser = StakeLine(uuid.uuid4(), uuid.uuid4(), "NON", 1001, START)

    plan = compute_settlement([first, second, loser], "OUI", 0, 0, bookie_fee_forfeited=False)

    credits = {credit.stake_id: credit.amount_cents for credit in plan.credits}
    assert credits == {second.stake_id: 1501, first.stake_id: 1500}
