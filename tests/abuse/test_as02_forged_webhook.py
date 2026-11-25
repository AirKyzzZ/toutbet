import io
import json

from fastapi.testclient import TestClient
from sqlalchemy import select

from support import Api, Player
from toutbet.models import Deposit, LedgerEntry
from toutbet.security.webhook_signature import sign_webhook


def _pending_deposit(api: Api) -> tuple[Player, str]:
    player = api.signup()
    return player, api.start_deposit(player, 2000).json()["deposit_id"]


def _assert_nothing_credited(api: Api, player: Player) -> None:
    assert api.balance(player) == 0
    with api.db() as db:
        assert db.scalars(select(Deposit.status)).all() == ["pending"]
        assert db.scalars(select(LedgerEntry)).all() == []


def test_as02_unsigned_webhook_rejected(
    api: Api, client: TestClient, captured_logs: io.StringIO
) -> None:
    player, deposit_id = _pending_deposit(api)
    body = json.dumps(api.payment_event(deposit_id))

    response = client.post(
        "/api/webhooks/psp", content=body, headers={"Content-Type": "application/json"}
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "INVALID_SIGNATURE"
    assert "psp.webhook_rejected" in captured_logs.getvalue()
    _assert_nothing_credited(api, player)


def test_as02_bad_signature_rejected(api: Api, client: TestClient) -> None:
    player, deposit_id = _pending_deposit(api)
    event = api.payment_event(deposit_id)
    secret = api.settings.psp_webhook_secret.get_secret_value().encode()
    original_header = sign_webhook(
        secret, int(api.clock.now().timestamp()), json.dumps(event).encode()
    )
    tampered_body = json.dumps({**event, "data": {**event["data"], "amount_cents": 50_000}})

    forged = api.signed_webhook(event, secret="attacker-guess-" + "x" * 40)
    replaced = client.post(
        "/api/webhooks/psp",
        content=tampered_body,
        headers={"Content-Type": "application/json", "X-PSP-Signature": original_header},
    )

    assert forged.status_code == replaced.status_code == 400
    assert forged.json()["error"]["code"] == "INVALID_SIGNATURE"
    _assert_nothing_credited(api, player)


def test_as02_stale_timestamp_rejected(api: Api) -> None:
    player, deposit_id = _pending_deposit(api)
    now = int(api.clock.now().timestamp())

    stale = api.signed_webhook(api.payment_event(deposit_id), timestamp=now - 301)
    future = api.signed_webhook(api.payment_event(deposit_id), timestamp=now + 301)

    assert stale.status_code == future.status_code == 400
    _assert_nothing_credited(api, player)


def test_as02_replayed_webhook_credits_once(api: Api) -> None:
    player, deposit_id = _pending_deposit(api)
    event = api.payment_event(deposit_id)

    responses = [api.signed_webhook(event) for _ in range(3)]

    assert [r.status_code for r in responses] == [200, 200, 200]
    assert api.balance(player) == 2000
    with api.db() as db:
        assert len(db.scalars(select(LedgerEntry)).all()) == 1


def test_as02_amount_or_currency_mismatch_not_credited(
    api: Api, captured_logs: io.StringIO
) -> None:
    player, deposit_id = _pending_deposit(api)

    api.signed_webhook(api.payment_event(deposit_id, amount_cents=50_000))
    api.signed_webhook(api.payment_event(deposit_id, currency="USD"))

    assert captured_logs.getvalue().count("psp.amount_mismatch") == 2
    _assert_nothing_credited(api, player)
