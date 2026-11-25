import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

from fastapi.testclient import TestClient
from sqlalchemy import select

from support import Api, staked_bet
from toutbet.models import Stake
from toutbet.services.verification import verify_ledger


def test_as07_stake_after_close_rejected_before_worker_runs(api: Api) -> None:
    scenario = staked_bet(api, [])
    player = api.signup()
    api.fund(player, 2000)
    api.add_participants(scenario.bookie, scenario.bet["id"], player)
    closes_at = datetime.fromisoformat(scenario.bet["closes_at"])
    api.clock.advance(closes_at - api.clock.now())
    api.relogin(player)

    response = api.stake(player, scenario.bet["id"], "OUI", 1000)

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "BET_CLOSED"
    assert api.bet(player, scenario.bet["id"])["status"] == "open"
    assert api.balance(player) == 2000


def test_as07_stake_cannot_be_modified_or_deleted(api: Api, client: TestClient) -> None:
    scenario = staked_bet(api, [("bob", "OUI", 1000)])
    bob = scenario.players["bob"]
    path = f"/api/bets/{scenario.bet['id']}/stakes"

    statuses = [
        client.request(method, path, json={"outcome": "NON"}, headers=bob.headers).status_code
        for method in ("PUT", "PATCH", "DELETE")
    ]

    assert statuses == [405, 405, 405]
    assert api.bet(bob, scenario.bet["id"])["my_stake"]["outcome"] == "OUI"


def test_as07_concurrent_stakes_never_overspend(api: Api, client: TestClient) -> None:
    bookie = api.signup("bookie")
    player = api.signup("player")
    api.fund(player, 10_000)
    bet_ids = [api.create_bet(bookie)["id"] for _ in range(50)]
    for bet_id in bet_ids:
        api.add_participants(bookie, bet_id, player)
    barrier = threading.Barrier(len(bet_ids))

    def stake(bet_id: str) -> int:
        headers = {**player.headers, "Idempotency-Key": str(uuid.uuid4())}
        barrier.wait()
        response = client.post(
            f"/api/bets/{bet_id}/stakes",
            json={"outcome": "OUI", "amount_cents": 1000},
            headers=headers,
        )
        return response.status_code

    with ThreadPoolExecutor(max_workers=len(bet_ids)) as pool:
        statuses = list(pool.map(stake, bet_ids))

    assert statuses.count(201) == 10
    assert statuses.count(409) == 40
    assert api.balance(player) == 0
    with api.db() as db:
        assert len(db.scalars(select(Stake)).all()) == 10
        report = verify_ledger(db)
    assert report.ok, report.message


def test_as07_replayed_stake_debits_once(api: Api) -> None:
    scenario = staked_bet(api, [])
    player = api.signup()
    api.fund(player, 3000)
    api.add_participants(scenario.bookie, scenario.bet["id"], player)
    key = str(uuid.uuid4())

    first = api.stake(player, scenario.bet["id"], "OUI", 1000, key=key)
    replay = api.stake(player, scenario.bet["id"], "OUI", 1000, key=key)

    assert first.status_code == replay.status_code == 201
    assert replay.json() == first.json()
    assert replay.headers["idempotent-replayed"] == "true"
    assert api.balance(player) == 2000


def test_as07_reused_key_with_other_body_rejected(api: Api) -> None:
    scenario = staked_bet(api, [])
    player = api.signup()
    api.fund(player, 3000)
    api.add_participants(scenario.bookie, scenario.bet["id"], player)
    key = str(uuid.uuid4())
    api.stake(player, scenario.bet["id"], "OUI", 1000, key=key)

    response = api.stake(player, scenario.bet["id"], "OUI", 2000, key=key)

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "IDEMPOTENCY_KEY_REUSED"
    assert api.balance(player) == 2000


def test_as07_missing_idempotency_key_rejected(api: Api, client: TestClient) -> None:
    scenario = staked_bet(api, [])
    player = api.signup()
    api.fund(player, 3000)
    api.add_participants(scenario.bookie, scenario.bet["id"], player)

    missing = client.post(
        f"/api/bets/{scenario.bet['id']}/stakes",
        json={"outcome": "OUI", "amount_cents": 1000},
        headers=player.headers,
    )
    malformed = api.stake(player, scenario.bet["id"], "OUI", 1000, key="not-a-uuid")

    assert missing.status_code == malformed.status_code == 422
    assert api.balance(player) == 3000
