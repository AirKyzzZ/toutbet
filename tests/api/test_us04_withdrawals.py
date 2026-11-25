from fastapi.testclient import TestClient
from sqlalchemy import select

from support import DEFAULT_PASSWORD, Api, Player, idempotency_headers
from toutbet.models import LedgerEntry, User, Withdrawal
from toutbet.psp.client import PspUnavailableError
from toutbet.services.verification import verify_ledger


def _kyc_status(api: Api, player: Player) -> str:
    with api.db() as db:
        return db.execute(select(User.kyc_status).where(User.email == player.email)).scalar_one()


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
    player = api.signup()
    api.fund(player, 5000)
    api.verify_kyc(player)
    api.relogin(player)
    api.psp.payout_error = PspUnavailableError("ReadTimeout")

    response = client.post(
        "/api/withdrawals",
        json={"amount_cents": 2000, "password": DEFAULT_PASSWORD},
        headers=idempotency_headers(player),
    )

    assert response.status_code == 503
    assert api.balance(player) == 5000
    with api.db() as db:
        assert db.scalars(select(Withdrawal.status)).all() == ["failed"]
        kinds = db.scalars(select(LedgerEntry.entry_type).order_by(LedgerEntry.seq)).all()
        assert kinds == ["deposit", "withdrawal", "withdrawal_reversal"]
        assert verify_ledger(db).ok
