from datetime import datetime, timedelta

from fastapi.routing import iter_route_contexts
from fastapi.testclient import TestClient
from sqlalchemy import select

from support import Api, idempotency_headers, staked_bet
from toutbet.models import Bet


def test_as08_closes_at_after_event_start_rejected(api: Api, client: TestClient) -> None:
    bookie = api.signup()
    now = api.clock.now()
    body = api.bet_body(
        closes_at=(now + timedelta(hours=3)).isoformat(),
        event_starts_at=(now + timedelta(hours=2)).isoformat(),
    )

    response = client.post("/api/bets", json=body, headers=bookie.headers)

    assert response.status_code == 422
    with api.db() as db:
        assert db.scalars(select(Bet)).all() == []


def test_as08_stake_at_closes_at_rejected(api: Api) -> None:
    scenario = staked_bet(api, [])
    player = api.signup()
    api.fund(player, 2000)
    api.add_participants(scenario.bookie, scenario.bet["id"], player)
    api.clock.set(datetime.fromisoformat(scenario.bet["closes_at"]))
    api.relogin(player)

    response = api.stake(player, scenario.bet["id"], "OUI", 1000)

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "BET_CLOSED"
    assert api.balance(player) == 2000


def test_as08_client_timestamp_rejected(api: Api, client: TestClient) -> None:
    scenario = staked_bet(api, [])
    player = api.signup()
    api.fund(player, 2000)
    api.add_participants(scenario.bookie, scenario.bet["id"], player)
    past = (api.clock.now() - timedelta(days=1)).isoformat()

    for field in ("placed_at", "created_at", "timestamp"):
        response = client.post(
            f"/api/bets/{scenario.bet['id']}/stakes",
            json={"outcome": "OUI", "amount_cents": 1000, field: past},
            headers=idempotency_headers(player),
        )
        assert response.status_code == 422

    assert api.balance(player) == 2000


def test_as08_no_route_modifies_event_dates(api: Api, client: TestClient) -> None:
    routes = {
        (method, context.path)
        for context in iter_route_contexts(client.app.routes)
        for method in context.methods or ()
    }
    bookie = api.signup()
    bet = api.create_bet(bookie)
    moved = (api.clock.now() + timedelta(days=10)).isoformat()

    attempts = [
        client.request(
            method,
            f"/api/bets/{bet['id']}{suffix}",
            json={"closes_at": moved},
            headers=bookie.headers,
        ).status_code
        for method in ("PUT", "PATCH", "POST")
        for suffix in ("", "/closes_at", "/dates")
    ]

    assert not any(method in {"PUT", "PATCH", "DELETE"} for method, _ in routes)
    assert not any("close" in path or "date" in path for _, path in routes)
    assert all(code in {404, 405} for code in attempts)
    assert api.bet(bookie, bet["id"])["closes_at"] == bet["closes_at"]
