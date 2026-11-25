import io
import json
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor

from fastapi.testclient import TestClient
from sqlalchemy import select

from support import DEFAULT_PASSWORD, Api, Player, idempotency_headers
from toutbet.models import User, Withdrawal
from toutbet.services.verification import verify_ledger


def _verified_player(api: Api, amount: int = 5000) -> Player:
    player = api.signup()
    api.fund(player, amount)
    api.verify_kyc(player)
    api.relogin(player)
    return player


def _withdraw(client: TestClient, player: Player, body: dict[str, object]) -> int:
    response = client.post("/api/withdrawals", json=body, headers=idempotency_headers(player))
    return response.status_code


def test_as04_withdrawal_requires_password(api: Api, client: TestClient) -> None:
    player = _verified_player(api)

    missing = _withdraw(client, player, {"amount_cents": 2000})
    wrong = _withdraw(client, player, {"amount_cents": 2000, "password": "stolen-token-only"})

    assert missing == wrong == 401
    assert api.balance(player) == 5000
    assert api.psp.payouts == []


def test_as04_destination_field_rejected(api: Api, client: TestClient) -> None:
    player = _verified_player(api)
    base = {"amount_cents": 2000, "password": DEFAULT_PASSWORD}

    for field, value in [
        ("iban", "FR7630006000011234567890189"),
        ("destination", "attacker"),
        ("bank_account_id", "acct_attacker"),
    ]:
        assert _withdraw(client, player, {**base, field: value}) == 422

    assert api.balance(player) == 5000
    assert api.psp.payouts == []


def test_as04_kyc_status_cannot_be_self_assigned(api: Api, client: TestClient) -> None:
    rejected = api.register(kyc_status="verified")
    player = api.signup()
    api.fund(player, 5000)
    client.post("/api/kyc/start", json={"kyc_status": "verified"}, headers=player.headers)
    unsigned = client.post(
        "/api/webhooks/psp",
        content=json.dumps(
            {"event_id": "evt_forged", "type": "kyc.verified", "data": {"reference": player.id}}
        ),
        headers={"Content-Type": "application/json"},
    )
    api.relogin(player)

    assert rejected.status_code == 422
    assert unsigned.status_code == 400
    with api.db() as db:
        status = db.execute(select(User.kyc_status).where(User.email == player.email)).scalar_one()
    assert status == "pending"
    assert _withdraw(client, player, {"amount_cents": 2000, "password": DEFAULT_PASSWORD}) == 403
    assert api.balance(player) == 5000


def _kyc_status(api: Api, player: Player) -> str:
    with api.db() as db:
        return db.execute(select(User.kyc_status).where(User.email == player.email)).scalar_one()


def test_as04_kyc_event_ignored_unless_verification_pending(
    api: Api, captured_logs: io.StringIO
) -> None:
    never_started = api.signup()
    verified = _verified_player(api)

    unsolicited = api.kyc_event(never_started, "kyc.verified")
    downgrade = api.kyc_event(verified, "kyc.rejected")

    assert unsolicited.status_code == downgrade.status_code == 200
    assert _kyc_status(api, never_started) == "none"
    assert _kyc_status(api, verified) == "verified"
    events = [json.loads(line) for line in captured_logs.getvalue().splitlines()]
    ignored = [e for e in events if e["event"] == "psp.kyc_event_ignored"]
    assert {(e["user_id"], e["event_type"]) for e in ignored} == {
        (never_started.id, "kyc.verified"),
        (verified.id, "kyc.rejected"),
    }


def test_as04_withdrawal_requires_verified_kyc(api: Api, client: TestClient) -> None:
    player = api.signup()
    api.fund(player, 5000)
    client.post("/api/kyc/start", headers=player.headers)
    api.relogin(player)

    response = client.post(
        "/api/withdrawals",
        json={"amount_cents": 2000, "password": DEFAULT_PASSWORD},
        headers=idempotency_headers(player),
    )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "KYC_REQUIRED"
    assert api.balance(player) == 5000


def test_as04_concurrent_withdrawals_never_overspend(api: Api, client: TestClient) -> None:
    player = _verified_player(api, amount=5000)
    attempts = 12
    barrier = threading.Barrier(attempts)

    def withdraw(_: int) -> int:
        headers = {**player.headers, "Idempotency-Key": str(uuid.uuid4())}
        barrier.wait()
        response = client.post(
            "/api/withdrawals",
            json={"amount_cents": 1000, "password": DEFAULT_PASSWORD},
            headers=headers,
        )
        return response.status_code

    with ThreadPoolExecutor(max_workers=attempts) as pool:
        statuses = list(pool.map(withdraw, range(attempts)))

    assert statuses.count(201) == 5
    assert statuses.count(409) == attempts - 5
    assert api.balance(player) == 0
    assert len(api.psp.payouts) == 5
    with api.db() as db:
        assert db.scalars(select(Withdrawal.status)).all() == ["sent"] * 5
        assert verify_ledger(db).ok
