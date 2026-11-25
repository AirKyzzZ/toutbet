from collections.abc import Callable

from fastapi.testclient import TestClient
from sqlalchemy import func, select

from support import Api, FakeClock, FakePsp, proposed_bet, staked_bet
from toutbet.models import Contest, User


def test_as10_contest_requires_stake(api: Api) -> None:
    scenario = staked_bet(api, [("alice", "OUI", 1000), ("bob", "NON", 1000)])
    lurker = api.signup("lurker")
    outsider = api.signup("outsider")
    api.add_participants(scenario.bookie, scenario.bet["id"], lurker)
    api.start_event(scenario.bet, *scenario.everyone, lurker, outsider)
    api.propose(scenario.bookie, scenario.bet["id"], "OUI")

    bookie = api.contest(scenario.bookie, scenario.bet["id"])
    joined_without_stake = api.contest(lurker, scenario.bet["id"])
    stranger = api.contest(outsider, scenario.bet["id"])

    assert bookie.status_code == joined_without_stake.status_code == 403
    assert stranger.status_code == 404
    with api.db() as db:
        assert db.scalars(select(Contest)).all() == []


def test_as10_second_contest_rejected(api: Api) -> None:
    scenario = proposed_bet(api, [("alice", "OUI", 9500), ("bob", "NON", 500)], outcome="OUI")
    bob = scenario.players["bob"]
    api.contest(bob, scenario.bet["id"])

    second = api.contest(bob, scenario.bet["id"], reason="Encore moi, toujours pas d'accord.")

    assert second.status_code == 409
    assert second.json()["error"]["code"] == "ALREADY_CONTESTED"
    with api.db() as db:
        assert db.scalar(select(func.count()).select_from(Contest)) == 1


def test_as10_small_stakes_do_not_reach_threshold(api: Api) -> None:
    sybils = [(f"sybil{i}", "NON", 100) for i in range(10)]
    scenario = proposed_bet(api, [("winner", "OUI", 50_000), *sybils], outcome="OUI")

    statuses = [
        api.contest(scenario.players[name], scenario.bet["id"]).json()["status"]
        for name, _, _ in sybils
    ]

    assert statuses == ["result_proposed"] * 10
    api.end_contest_window(*scenario.everyone)
    assert api.bet(scenario.bookie, scenario.bet["id"])["status"] == "settled"


def test_as10_registration_rate_limited_per_ip(
    make_client: Callable[..., TestClient], clock: FakeClock, psp: FakePsp
) -> None:
    api = Api(make_client(rate_limit_register="5/hour"), clock, psp)

    statuses = [api.register().status_code for _ in range(6)]

    assert statuses == [202] * 5 + [429]
    with api.db() as db:
        assert db.scalar(select(func.count()).select_from(User)) == 5


def test_as10_email_case_variants_are_one_account(api: Api) -> None:
    for index, email in enumerate(["Mule@Example.org", "mule@example.org", " MULE@EXAMPLE.ORG "]):
        assert api.register(email=email, display_name=f"mule_{index}").status_code == 202

    with api.db() as db:
        emails = db.scalars(select(User.email)).all()
    assert emails == ["mule@example.org"]
