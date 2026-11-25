import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from support import Api, staked_bet
from toutbet.models import Bet


def _no_bet_created(api: Api) -> bool:
    with api.db() as db:
        return db.scalars(select(Bet)).all() == []


def test_as05_commission_above_cap_rejected(api: Api, client: TestClient) -> None:
    bookie = api.signup()

    for commission in (1001, 10_000, -1):
        body = api.bet_body(bookie_commission_bps=commission)
        assert client.post("/api/bets", json=body, headers=bookie.headers).status_code == 422

    assert _no_bet_created(api)


@pytest.mark.parametrize(
    "field",
    ["bookie_id", "status", "platform_commission_bps", "proposed_outcome", "role"],
)
def test_as05_unknown_fields_rejected(api: Api, client: TestClient, field: str) -> None:
    bookie = api.signup()
    other = api.signup()
    value = {"bookie_id": other.id, "platform_commission_bps": 0, "status": "settled"}.get(
        field, "OUI"
    )

    response = client.post("/api/bets", json=api.bet_body(**{field: value}), headers=bookie.headers)

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"
    assert _no_bet_created(api)


@pytest.mark.parametrize("amount", [10.5, "1000", True, -100])
def test_as05_non_integer_amounts_rejected(api: Api, client: TestClient, amount: object) -> None:
    scenario = staked_bet(api, [])
    bookie = scenario.bookie
    player = api.signup()
    api.fund(player, 5000)
    api.add_participants(bookie, scenario.bet["id"], player)

    bet_response = client.post(
        "/api/bets", json=api.bet_body(min_stake_cents=amount), headers=bookie.headers
    )
    commission_response = client.post(
        "/api/bets", json=api.bet_body(bookie_commission_bps=amount), headers=bookie.headers
    )
    stake_response = api.stake(player, scenario.bet["id"], "OUI", amount)

    assert bet_response.status_code == commission_response.status_code == 422
    assert stake_response.status_code == 422
    assert api.balance(player) == 5000


def test_as05_bet_cannot_be_modified(api: Api, client: TestClient) -> None:
    bookie = api.signup()
    bet = api.create_bet(bookie)
    path = f"/api/bets/{bet['id']}"

    statuses = [
        client.request(method, path, json={"bookie_commission_bps": 1000}, headers=bookie.headers)
        for method in ("PUT", "PATCH", "DELETE")
    ]

    assert [response.status_code for response in statuses] == [405, 405, 405]
    assert statuses[0].json()["error"]["code"] == "METHOD_NOT_ALLOWED"
    assert api.bet(bookie, bet["id"])["bookie_commission_bps"] == 400


def test_as05_html_title_returned_verbatim_with_csp(api: Api, client: TestClient) -> None:
    bookie = api.signup()
    title = '<script>alert("pwned")</script><img src=x onerror=alert(1)>'

    bet = api.create_bet(bookie, title=title, description="<b>gras</b>")
    response = client.get(f"/api/bets/{bet['id']}", headers=bookie.headers)

    assert response.json()["title"] == title
    assert response.json()["description"] == "<b>gras</b>"
    assert response.headers["content-type"].startswith("application/json")
    csp = response.headers["content-security-policy"]
    assert "script-src 'self'" in csp
    assert "unsafe-inline" not in csp
    assert "unsafe-eval" not in csp
    index = client.get("/")
    assert "unsafe-inline" not in index.headers["content-security-policy"]
