from collections.abc import Callable

from fastapi.testclient import TestClient
from sqlalchemy import func, select

from support import DEFAULT_PASSWORD, Api, FakeClock, FakePsp, idempotency_headers
from toutbet.models import Bet, Deposit, Stake, Withdrawal


def test_feature_switch_returns_503(
    make_client: Callable[..., TestClient], clock: FakeClock, psp: FakePsp
) -> None:
    open_api = Api(make_client(), clock, psp)
    bookie, player = open_api.signup(), open_api.signup()
    open_api.fund(player, 5000)
    open_api.verify_kyc(player)
    bet = open_api.create_bet(bookie)
    open_api.add_participants(bookie, bet["id"], player)
    closed = make_client(
        bet_creation_enabled=False,
        stakes_enabled=False,
        deposits_enabled=False,
        withdrawals_enabled=False,
    )
    api = Api(closed, clock, psp)
    api.relogin(bookie, player)

    responses = [
        closed.post("/api/bets", json=api.bet_body(), headers=bookie.headers),
        api.stake(player, bet["id"], "OUI", 1000),
        api.start_deposit(player, 1000),
        closed.post(
            "/api/withdrawals",
            json={"amount_cents": 1000, "password": DEFAULT_PASSWORD},
            headers=idempotency_headers(player),
        ),
    ]

    assert [r.status_code for r in responses] == [503, 503, 503, 503]
    assert {r.json()["error"]["code"] for r in responses} == {"FEATURE_DISABLED"}
    with api.db() as db:
        assert db.scalar(select(func.count()).select_from(Bet)) == 1
        assert db.scalar(select(func.count()).select_from(Stake)) == 0
        assert db.scalar(select(func.count()).select_from(Deposit)) == 1
        assert db.scalar(select(func.count()).select_from(Withdrawal)) == 0
    assert api.balance(player) == 5000
    assert closed.get(f"/api/bets/{bet['id']}", headers=player.headers).status_code == 200


def test_bet_creation_switch_keeps_the_rest_usable(
    make_client: Callable[..., TestClient], clock: FakeClock, psp: FakePsp
) -> None:
    open_api = Api(make_client(), clock, psp)
    bookie, player = open_api.signup(), open_api.signup()
    open_api.fund(player, 5000)
    bet = open_api.create_bet(bookie)
    open_api.add_participants(bookie, bet["id"], player)
    assert open_api.client.get("/api/me", headers=player.headers).json()["bet_creation_enabled"]
    closed = make_client(bet_creation_enabled=False)
    api = Api(closed, clock, psp)
    api.relogin(bookie, player)

    refused = closed.post("/api/bets", json=api.bet_body(), headers=bookie.headers)

    assert refused.status_code == 503
    assert refused.json()["error"]["message"].startswith(
        "La création de paris est momentanément indisponible."
    )
    assert closed.get("/api/me", headers=bookie.headers).json()["bet_creation_enabled"] is False
    assert closed.get("/api/bets", headers=bookie.headers).json()["items"][0]["id"] == bet["id"]
    assert api.stake(player, bet["id"], "OUI", 1000).status_code == 201
    assert api.balance(player) == 4000
    assert api.start_deposit(player, 1000).status_code == 201
