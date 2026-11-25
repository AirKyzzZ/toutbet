import io
import uuid

from fastapi.testclient import TestClient
from sqlalchemy import select

from support import Api, idempotency_headers
from toutbet.models import Deposit, LedgerEntry
from toutbet.psp.client import PspUnavailableError
from toutbet.services.verification import verify_ledger


def test_us02_1_deposit_creates_pending_record_and_returns_hosted_url(api: Api) -> None:
    player = api.signup()

    response = api.start_deposit(player, 2000)

    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "pending"
    checkout = api.psp.checkouts[body["deposit_id"]]
    assert body["redirect_url"] == f"https://psp.test/pay/{checkout['session_id']}"
    assert checkout["amount_cents"] == 2000
    assert checkout["currency"] == "EUR"
    with api.db() as db:
        deposit = db.get(Deposit, uuid.UUID(body["deposit_id"]))
        assert deposit is not None
        assert deposit.status == "pending"
        assert deposit.psp_session_id == checkout["session_id"]
    assert api.start_deposit(player, 499).status_code == 422
    assert api.start_deposit(player, 50_001).status_code == 422
    assert api.balance(player) == 0


def test_us02_2_card_data_is_never_received_stored_or_logged(
    api: Api, client: TestClient, captured_logs: io.StringIO
) -> None:
    player = api.signup()
    card_number = "4111111111111111"

    response = client.post(
        "/api/deposits",
        json={"amount_cents": 2000, "card_number": card_number, "cvv": "123"},
        headers=idempotency_headers(player),
    )

    assert response.status_code == 422
    assert card_number not in response.text
    assert card_number not in captured_logs.getvalue()
    assert api.psp.checkouts == {}
    with api.db() as db:
        assert db.scalars(select(Deposit)).all() == []
    accepted = api.start_deposit(player, 2000)
    checkout = api.psp.checkouts[accepted.json()["deposit_id"]]
    assert set(checkout) == {"session_id", "amount_cents", "currency", "return_url"}


def test_us02_3_signed_webhook_credits_wallet_and_ledger_exactly_once(api: Api) -> None:
    player = api.signup()
    deposit_id = api.start_deposit(player, 2500).json()["deposit_id"]
    event = api.payment_event(deposit_id)

    assert api.signed_webhook(event).status_code == 200
    assert api.signed_webhook(event).status_code == 200

    assert api.balance(player) == 2500
    with api.db() as db:
        entries = db.scalars(select(LedgerEntry).where(LedgerEntry.entry_type == "deposit")).all()
        assert [(e.from_account, e.amount_cents) for e in entries] == [("psp:clearing", 2500)]
        assert entries[0].to_account == f"wallet:{player.id}"
        assert verify_ledger(db).ok


def test_us02_4_deposit_stays_pending_until_webhook_even_after_browser_return(
    api: Api, client: TestClient
) -> None:
    player = api.signup()
    deposit_id = api.start_deposit(player, 2000).json()["deposit_id"]

    browser_return = client.get(
        f"/api/deposits/{deposit_id}?status=succeeded&paid=true", headers=player.headers
    )

    assert browser_return.json()["status"] == "pending"
    assert api.balance(player) == 0
    api.signed_webhook(api.payment_event(deposit_id))
    assert client.get(f"/api/deposits/{deposit_id}", headers=player.headers).json()["status"] == (
        "succeeded"
    )


def test_us02_5_psp_timeout_marks_deposit_failed(api: Api, client: TestClient) -> None:
    player = api.signup()
    api.psp.checkout_error = PspUnavailableError("ReadTimeout")
    key = str(uuid.uuid4())

    response = client.post(
        "/api/deposits", json={"amount_cents": 2000}, headers=idempotency_headers(player, key)
    )

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "PSP_UNAVAILABLE"
    with api.db() as db:
        statuses = db.scalars(select(Deposit.status)).all()
        assert statuses == ["failed"]
    assert api.balance(player) == 0
    assert client.get("/healthz").status_code == 200
    api.psp.checkout_error = None
    retry = client.post(
        "/api/deposits", json={"amount_cents": 2000}, headers=idempotency_headers(player, key)
    )
    assert retry.status_code == 201
