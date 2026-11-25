from datetime import timedelta

from sqlalchemy import select

from support import Api, proposed_bet
from toutbet.models import Contest


def test_us10_1_one_contest_per_staking_participant_while_window_open(api: Api) -> None:
    scenario = proposed_bet(api, [("alice", "OUI", 9500), ("bob", "NON", 500)], outcome="OUI")
    bob = scenario.players["bob"]

    first = api.contest(bob, scenario.bet["id"], reason="Matis a calé deux fois.")
    second = api.contest(bob, scenario.bet["id"], reason="Je conteste encore une fois.")
    too_short = api.contest(scenario.players["alice"], scenario.bet["id"], reason="court")

    assert first.status_code == 201
    assert first.json()["my_contested"] is True
    assert second.status_code == 409
    assert too_short.status_code == 422


def test_us10_1_reason_counts_only_non_whitespace_characters(api: Api) -> None:
    scenario = proposed_bet(api, [("alice", "OUI", 9500), ("bob", "NON", 500)], outcome="OUI")
    bob = scenario.players["bob"]

    blank = api.contest(bob, scenario.bet["id"], reason=" " * 20)
    padded = api.contest(bob, scenario.bet["id"], reason="\t non   faux \n" + " " * 10)

    assert blank.status_code == padded.status_code == 422
    with api.db() as db:
        assert db.scalars(select(Contest)).all() == []
    assert api.contest(bob, scenario.bet["id"], reason="  Matis a calé.  ").status_code == 201


def test_us10_2_contests_reaching_10_percent_of_stakes_dispute_the_bet(api: Api) -> None:
    scenario = proposed_bet(
        api, [("alice", "OUI", 9000), ("bob", "NON", 900), ("carol", "NON", 100)], outcome="OUI"
    )

    below = api.contest(scenario.players["bob"], scenario.bet["id"])
    reached = api.contest(scenario.players["carol"], scenario.bet["id"])

    assert below.json()["status"] == "result_proposed"
    assert reached.json()["status"] == "disputed"
    api.end_contest_window(*scenario.everyone)
    assert api.bet(scenario.bookie, scenario.bet["id"])["status"] == "disputed"
    assert api.balance(scenario.players["alice"]) == 0


def test_us10_3_contest_after_window_rejected(api: Api) -> None:
    scenario = proposed_bet(api, [("alice", "OUI", 1000), ("bob", "NON", 1000)], outcome="OUI")
    api.clock.advance(timedelta(hours=24))
    api.relogin(*scenario.everyone)

    response = api.contest(scenario.players["bob"], scenario.bet["id"])

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "CONTEST_WINDOW_CLOSED"


def test_us10_4_window_end_without_threshold_settles_on_proposed_result(api: Api) -> None:
    scenario = proposed_bet(
        api, [("alice", "OUI", 9500), ("bob", "NON", 500)], outcome="OUI", bookie_commission_bps=0
    )
    api.contest(scenario.players["bob"], scenario.bet["id"])

    api.end_contest_window(*scenario.everyone)

    bet = api.bet(scenario.bookie, scenario.bet["id"])
    assert bet["status"] == "settled"
    assert bet["final_outcome"] == "OUI"
    assert api.balance(scenario.players["alice"]) == 9500 + 500 - 25
    assert api.balance(scenario.players["bob"]) == 0
