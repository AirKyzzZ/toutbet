import uuid
from datetime import timedelta

from fastapi.testclient import TestClient
from sqlalchemy import select

from support import Api, staked_bet
from toutbet.models import Bet
from toutbet.services.verification import verify_ledger


def test_us05_1_bet_created_open_with_bookie_and_platform_commission(
    api: Api, client: TestClient
) -> None:
    bookie = api.signup("bookie")

    bet = api.create_bet(bookie, min_stake_cents=500, bookie_commission_bps=1000)

    assert bet["status"] == "open"
    assert bet["bookie"] == "bookie"
    assert bet["my_role"] == "bookie"
    assert bet["platform_commission_bps"] == 500
    assert bet["bookie_commission_bps"] == 1000
    assert bet["min_stake_cents"] == 500
    with api.db() as db:
        stored = db.get(Bet, uuid.UUID(bet["id"]))
        assert stored is not None
        assert str(stored.bookie_id) == bookie.id
    assert api.create_bet(bookie, description=None)["description"] is None
    long_title = api.bet_body(title="x" * 141)
    assert client.post("/api/bets", json=long_title, headers=bookie.headers).status_code == 422


def test_us05_2_invalid_dates_rejected(api: Api, client: TestClient) -> None:
    bookie = api.signup()
    now = api.clock.now()
    cases = [
        (now + timedelta(minutes=4), now + timedelta(hours=1)),
        (now + timedelta(hours=3), now + timedelta(hours=2)),
        (now + timedelta(days=1), now + timedelta(days=91)),
    ]
    bodies = [
        api.bet_body(closes_at=closes.isoformat(), event_starts_at=starts.isoformat())
        for closes, starts in cases
    ]
    bodies.append(
        api.bet_body(closes_at="2030-01-01T10:00:00", event_starts_at="2030-01-01T12:00:00")
    )

    for body in bodies:
        assert client.post("/api/bets", json=body, headers=bookie.headers).status_code == 422

    with api.db() as db:
        assert db.scalars(select(Bet)).all() == []


def test_us05_3_exactly_two_outcomes(api: Api) -> None:
    scenario = staked_bet(api, [("bob", "OUI", 1000)])
    carol = api.signup("carol")
    api.fund(carol, 1000)
    api.add_participants(scenario.bookie, scenario.bet["id"], carol)

    response = api.stake(carol, scenario.bet["id"], "PEUT-ETRE", 500)

    assert response.status_code == 422
    assert set(api.bet(carol, scenario.bet["id"])["totals"]) == {"OUI", "NON"}
    assert api.balance(carol) == 1000


def test_us05_4_bet_parameters_cannot_change_after_creation(api: Api, client: TestClient) -> None:
    bookie = api.signup()
    bet = api.create_bet(bookie)
    changes = {"closes_at": (api.clock.now() + timedelta(days=5)).isoformat(), "title": "Autre"}

    for method in ("PUT", "PATCH"):
        response = client.request(
            method, f"/api/bets/{bet['id']}", json=changes, headers=bookie.headers
        )
        assert response.status_code == 405

    after = api.bet(bookie, bet["id"])
    for field in (
        "title",
        "closes_at",
        "event_starts_at",
        "bookie_commission_bps",
        "min_stake_cents",
    ):
        assert after[field] == bet[field]


def test_us05_5_cancelling_open_bet_refunds_every_stake(api: Api, client: TestClient) -> None:
    scenario = staked_bet(api, [("bob", "OUI", 1000), ("carol", "NON", 2500)])

    response = client.post(
        f"/api/bets/{scenario.bet['id']}/cancel", headers=scenario.bookie.headers
    )

    assert response.status_code == 200
    assert response.json()["status"] == "cancelled"
    assert api.balance(scenario.players["bob"]) == 1000
    assert api.balance(scenario.players["carol"]) == 2500
    assert api.balance(scenario.bookie) == 0
    with api.db() as db:
        assert verify_ledger(db).ok
    again = client.post(f"/api/bets/{scenario.bet['id']}/cancel", headers=scenario.bookie.headers)
    assert again.status_code == 409


def test_us05_6_moderator_cannot_create_bet(api: Api, client: TestClient) -> None:
    moderator = api.moderator()

    response = client.post("/api/bets", json=api.bet_body(), headers=moderator.headers)

    assert response.status_code == 403
    with api.db() as db:
        assert db.scalars(select(Bet)).all() == []
