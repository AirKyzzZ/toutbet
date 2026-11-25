import io
import json
import uuid
from datetime import timedelta

from fastapi.testclient import TestClient
from httpx import Response
from sqlalchemy import func, select

from support import DEFAULT_PASSWORD, Api, Player, idempotency_headers
from toutbet.models import IdempotencyRecord, LedgerEntry, User, Withdrawal
from toutbet.psp.client import PspRefusedError, PspUnavailableError
from toutbet.services.verification import verify_ledger


def _kyc_status(api: Api, player: Player) -> str:
    with api.db() as db:
        return db.execute(select(User.kyc_status).where(User.email == player.email)).scalar_one()


def _verified_player(api: Api) -> Player:
    player = api.signup()
    api.fund(player, 5000)
    api.verify_kyc(player)
    api.relogin(player)
    return player


def _withdraw(client: TestClient, player: Player, key: str | None = None) -> Response:
    return client.post(
        "/api/withdrawals",
        json={"amount_cents": 2000, "password": DEFAULT_PASSWORD},
        headers=idempotency_headers(player, key),
    )


def _withdrawal_state(api: Api) -> tuple[list[str], list[str], int]:
    with api.db() as db:
        statuses = list(db.scalars(select(Withdrawal.status)).all())
        kinds = list(db.scalars(select(LedgerEntry.entry_type).order_by(LedgerEntry.seq)).all())
        keys = db.scalar(select(func.count()).select_from(IdempotencyRecord)) or 0
        assert verify_ledger(db).ok
    return statuses, kinds, keys


def test_us04_1_kyc_start_returns_hosted_url_without_identity_documents(
    api: Api, client: TestClient
) -> None:
    player = api.signup()

    response = client.post("/api/kyc/start", headers=player.headers)

    assert response.status_code == 200
    assert response.json()["redirect_url"].startswith("https://psp.test/kyc/")
    assert api.psp.kyc_sessions[0]["reference"] == player.id
    assert set(api.psp.kyc_sessions[0]) == {"reference", "session_id"}
    assert _kyc_status(api, player) == "pending"


def test_us04_2_signed_kyc_webhook_marks_user_verified(api: Api) -> None:
    player = api.signup()

    api.verify_kyc(player)

    assert _kyc_status(api, player) == "verified"


def test_us04_3_verified_user_withdraws_atomically_to_psp_account(
    api: Api, client: TestClient
) -> None:
    player = api.signup()
    api.fund(player, 5000)
    api.verify_kyc(player)
    api.relogin(player)

    response = client.post(
        "/api/withdrawals",
        json={"amount_cents": 2000, "password": DEFAULT_PASSWORD},
        headers=idempotency_headers(player),
    )

    assert response.status_code == 201
    assert response.json()["status"] == "sent"
    assert api.balance(player) == 3000
    assert api.psp.payouts == [
        {"reference": response.json()["id"], "user": player.id, "amount_cents": 2000}
    ]
    with api.db() as db:
        entry = db.scalars(select(LedgerEntry).where(LedgerEntry.entry_type == "withdrawal")).one()
        assert (entry.from_account, entry.to_account) == (f"wallet:{player.id}", "psp:clearing")
        assert verify_ledger(db).ok


def test_us04_4_withdrawal_refused_without_verified_kyc(api: Api, client: TestClient) -> None:
    player = api.signup()
    api.fund(player, 5000)

    response = client.post(
        "/api/withdrawals",
        json={"amount_cents": 2000, "password": DEFAULT_PASSWORD},
        headers=idempotency_headers(player),
    )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "KYC_REQUIRED"
    assert api.balance(player) == 5000
    assert api.psp.payouts == []


def test_us04_5_psp_failure_reverses_withdrawal(api: Api, client: TestClient) -> None:
    player = _verified_player(api)
    api.psp.payout_error = PspRefusedError("status 402")
    key = str(uuid.uuid4())

    response = _withdraw(client, player, key)

    assert response.status_code == 503
    assert api.balance(player) == 5000
    assert _withdrawal_state(api) == (
        ["failed"],
        ["deposit", "withdrawal", "withdrawal_reversal"],
        1,
    )
    api.psp.payout_error = None
    assert _withdraw(client, player, key).status_code == 201


def test_us04_5_psp_timeout_keeps_withdrawal_pending(
    api: Api, client: TestClient, captured_logs: io.StringIO
) -> None:
    player = _verified_player(api)
    api.psp.payout_error = PspUnavailableError("ReadTimeout")

    response = _withdraw(client, player)

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "WITHDRAWAL_PENDING"
    assert "attente" in response.json()["error"]["message"]
    assert api.balance(player) == 3000
    assert _withdrawal_state(api) == (["pending"], ["deposit", "withdrawal"], 2)
    events = [json.loads(line) for line in captured_logs.getvalue().splitlines()]
    unconfirmed = [e for e in events if e["event"] == "money.withdrawal_unconfirmed"]
    assert len(unconfirmed) == 1
    assert not [e for e in events if e["event"] == "money.withdrawal_failed"]


def test_us04_5_timeout_then_retry_never_pays_twice(api: Api, client: TestClient) -> None:
    player = _verified_player(api)
    api.psp.payout_reply_error = PspUnavailableError("ReadTimeout")
    key = str(uuid.uuid4())

    first = _withdraw(client, player, key)
    api.psp.payout_reply_error = None
    retry = _withdraw(client, player, key)
    api.clock.advance(timedelta(minutes=10))
    api.tick()
    after_reconciliation = _withdraw(client, player, key)

    assert first.status_code == 503
    assert retry.status_code == after_reconciliation.status_code == 409
    assert len(api.psp.payouts) == 1
    with api.db() as db:
        withdrawal = db.scalars(select(Withdrawal)).one()
    assert api.psp.payouts[0]["reference"] == str(withdrawal.id)
    assert withdrawal.status == "sent"
    assert withdrawal.psp_payout_id == api.psp.payout_statuses[str(withdrawal.id)].payout_id
    assert api.balance(player) == 3000
    assert _withdrawal_state(api)[:2] == (["sent"], ["deposit", "withdrawal"])
