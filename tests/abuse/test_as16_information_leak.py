import io
import json
from collections.abc import Callable
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event

from support import DEFAULT_PASSWORD, Api, FakeClock, FakePsp, idempotency_headers
from toutbet.security.webhook_signature import sign_webhook


def test_as16_invalid_uuid_error_does_not_echo_characters(api: Api, client: TestClient) -> None:
    player = api.signup()
    marker = "Q"

    responses = [
        client.get(
            f"/api/bets/{marker}0000000-0000-0000-0000-000000000000", headers=player.headers
        ),
        client.post(
            f"/api/bets/{'0' * 8}-0000-0000-0000-000000000000/stakes",
            json={"outcome": "OUI", "amount_cents": 100},
            headers={**player.headers, "Idempotency-Key": f"1234{marker}"},
        ),
    ]

    for response in responses:
        assert response.status_code == 422
        assert f"`{marker}`" not in response.text
        messages = {field["msg"] for field in response.json()["error"]["fields"]}
        assert messages == {"Input should be a valid UUID"}


def test_as16_unhandled_error_returns_generic_body(
    api: Api, client: TestClient, captured_logs: io.StringIO
) -> None:
    player = api.signup()
    api.psp.checkout_error = RuntimeError("boom at /srv/toutbet/secret_module.py line 42")

    response = client.post(
        "/api/deposits", json={"amount_cents": 1000}, headers=idempotency_headers(player)
    )

    assert response.status_code == 500
    body = response.json()
    assert body["error"]["code"] == "INTERNAL_ERROR"
    assert body["error"]["request_id"] == response.headers["x-request-id"]
    for leak in ("Traceback", "secret_module", "RuntimeError", "toutbet", ".py"):
        assert leak not in response.text
    assert "content-security-policy" in response.headers
    logs = captured_logs.getvalue()
    assert "error.unhandled" in logs
    assert "Traceback" in logs


def test_as16_validation_error_does_not_echo_input(api: Api, client: TestClient) -> None:
    bookie = api.signup()
    marker = "INJECTED-7f3a<script>"

    response = client.post(
        "/api/bets",
        json={"title": marker, "bookie_commission_bps": marker, "closes_at": marker},
        headers=bookie.headers,
    )

    assert response.status_code == 422
    body = response.json()
    assert body["error"]["code"] == "VALIDATION_ERROR"
    assert "INJECTED" not in response.text
    locations = {tuple(field["loc"]) for field in body["error"]["fields"]}
    assert ("body", "bookie_commission_bps") in locations
    malformed = client.post(
        "/api/bets",
        content=b'{"title": "INJECTED-json',
        headers={**bookie.headers, "Content-Type": "application/json"},
    )
    assert malformed.status_code == 422
    assert "INJECTED" not in malformed.text


@pytest.mark.parametrize(
    "query",
    [
        "status=open' OR '1'='1",
        "status=open;DROP TABLE bets",
        "role=bookie UNION SELECT password_hash FROM users",
        "page=1 OR 1=1",
    ],
)
def test_as16_sql_injection_in_filters_rejected(api: Api, client: TestClient, query: str) -> None:
    bookie = api.signup()
    api.create_bet(bookie)

    response = client.get(f"/api/bets?{query}", headers=bookie.headers)

    assert response.status_code == 422
    assert "argon2" not in response.text
    assert len(client.get("/api/bets", headers=bookie.headers).json()["items"]) == 1


@pytest.mark.parametrize(
    "path",
    [
        "/../main.py",
        "/%2e%2e/main.py",
        "/..%2fmain.py",
        "/js/..%2f..%2fconfig.py",
        "/%2e%2e%2f%2e%2e%2f.env",
    ],
)
def test_as16_static_path_traversal_returns_404(client: TestClient, path: str) -> None:
    response = client.get(path)

    assert response.status_code == 404
    assert "create_app" not in response.text
    assert "SecretStr" not in response.text


def test_as16_docs_disabled_outside_dev(make_client: Callable[..., TestClient]) -> None:
    production = make_client(env="prod")
    development = make_client(env="dev")

    for path in ("/docs", "/redoc", "/openapi.json"):
        assert production.get(path).status_code == 404
    assert development.get("/openapi.json").status_code == 200


def test_as16_logs_never_contain_secrets(
    make_client: Callable[..., TestClient],
    clock: FakeClock,
    psp: FakePsp,
    captured_logs: io.StringIO,
) -> None:
    api = Api(make_client(), clock, psp)
    player = api.signup()
    response = api.start_deposit(player, 5000)
    event = api.payment_event(response.json()["deposit_id"])
    body = json.dumps(event).encode()
    secret = api.settings.psp_webhook_secret.get_secret_value()

    signature = sign_webhook(secret.encode(), int(clock.now().timestamp()), body)
    api.client.post(
        "/api/webhooks/psp",
        content=body,
        headers={"Content-Type": "application/json", "X-PSP-Signature": signature},
    )
    api.verify_kyc(player)
    api.relogin(player)
    api.client.post(
        "/api/withdrawals",
        json={"amount_cents": 1000, "password": DEFAULT_PASSWORD},
        headers=idempotency_headers(player),
    )
    api.client.post(
        "/api/withdrawals",
        json={"amount_cents": 1000, "password": "wrong password but secret"},
        headers=idempotency_headers(player),
    )

    logs = captured_logs.getvalue()
    assert "http.request" in logs
    for sensitive in (
        DEFAULT_PASSWORD,
        "wrong password but secret",
        player.token,
        secret,
        signature,
        signature.split("v1=")[1],
        api.settings.jwt_secret.get_secret_value(),
        player.email,
    ):
        assert sensitive not in logs
    for line in logs.splitlines():
        json.loads(line)


def test_as16_database_error_never_logs_bound_parameters(
    api: Api, client: TestClient, captured_logs: io.StringIO
) -> None:
    engine = client.app.state.engine

    def break_user_insert(
        _conn: Any, _cursor: Any, statement: str, parameters: Any, *_: Any
    ) -> tuple[str, Any]:
        return statement.replace("INSERT INTO users ", "INSERT INTO users_missing "), parameters

    event.listen(engine, "before_cursor_execute", break_user_insert, retval=True)
    try:
        response = api.register(birth_date="1987-11-23")
    finally:
        event.remove(engine, "before_cursor_execute", break_user_insert)

    assert response.status_code == 500
    logs = captured_logs.getvalue()
    assert "error.unhandled" in logs
    assert "users_missing" in logs
    for leak in ("1987-11-23", "1987, 11, 23", "$argon2", "argon2id", DEFAULT_PASSWORD):
        assert leak not in logs
