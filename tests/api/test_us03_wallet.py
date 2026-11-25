from fastapi.testclient import TestClient

from support import Api, proposed_bet
from toutbet.services.verification import verify_ledger


def test_us03_1_wallet_returns_balance_and_entries_paginated(api: Api, client: TestClient) -> None:
    player = api.signup()
    for amount in (500, 700, 900):
        api.fund(player, amount)

    first = client.get("/api/wallet?page_size=2", headers=player.headers).json()
    second = client.get("/api/wallet?page_size=2&page=2", headers=player.headers).json()

    assert first["balance_cents"] == 2100
    assert [e["amount_cents"] for e in first["entries"]] == [900, 700]
    assert [e["amount_cents"] for e in second["entries"]] == [500]
    assert all(e["direction"] == "credit" for e in first["entries"] + second["entries"])
    assert "seq" not in first["entries"][0]
    assert client.get("/api/wallet?page_size=51", headers=player.headers).status_code == 422


def test_us03_2_wallet_comes_from_token_identity_only(api: Api, client: TestClient) -> None:
    alice = api.signup("alice")
    bob = api.signup("bob")
    api.fund(alice, 5000)

    response = client.get(f"/api/wallet?user_id={alice.id}", headers=bob.headers)

    assert response.status_code == 200
    assert response.json()["balance_cents"] == 0
    assert response.json()["entries"] == []
    assert client.get(f"/api/wallet/{alice.id}", headers=bob.headers).status_code == 404


def test_us03_3_owner_reads_deposit_status(api: Api, client: TestClient) -> None:
    player = api.signup()
    deposit_id = api.start_deposit(player, 1500).json()["deposit_id"]

    response = client.get(f"/api/deposits/{deposit_id}", headers=player.headers)

    assert response.status_code == 200
    assert response.json()["status"] == "pending"
    assert response.json()["amount_cents"] == 1500


def test_us03_4_each_balance_equals_its_ledger_sum(api: Api) -> None:
    scenario = proposed_bet(api, [("alice", "OUI", 1000), ("bob", "NON", 3000)], outcome="OUI")
    api.end_contest_window(*scenario.everyone)

    with api.db() as db:
        report = verify_ledger(db)

    assert report.ok, report.message
    assert api.balance(scenario.players["alice"]) > 1000
