import io
from collections.abc import Callable
from datetime import timedelta
from typing import Any

import jwt
import pytest
from argon2 import PasswordHasher
from fastapi.testclient import TestClient
from sqlalchemy import event

from support import DEFAULT_PASSWORD, Api, FakeClock, FakePsp


def _body_without_request_id(response: Any) -> dict[str, Any]:
    body: dict[str, Any] = response.json()
    if "error" in body:
        body["error"].pop("request_id", None)
    return body


@pytest.fixture
def argon2_calls(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    calls: list[str] = []
    original_hash, original_verify = PasswordHasher.hash, PasswordHasher.verify

    def spy_hash(self: PasswordHasher, password: str | bytes, *, salt: bytes | None = None) -> str:
        calls.append("hash")
        return original_hash(self, password, salt=salt)

    def spy_verify(self: PasswordHasher, hash: str | bytes, password: str | bytes) -> bool:
        calls.append("verify")
        return original_verify(self, hash, password)

    monkeypatch.setattr(PasswordHasher, "hash", spy_hash)
    monkeypatch.setattr(PasswordHasher, "verify", spy_verify)
    return calls


def test_as01_login_rate_limited_per_ip(
    make_client: Callable[..., TestClient], clock: FakeClock, psp: FakePsp
) -> None:
    api = Api(make_client(rate_limit_login="5/minute"), clock, psp)

    responses = [api.login("victim@example.org", f"guess-{i}-password") for i in range(6)]

    assert [r.status_code for r in responses] == [401] * 5 + [429]
    assert responses[5].json()["error"]["code"] == "RATE_LIMITED"
    assert int(responses[5].headers["retry-after"]) > 0


def test_as01_spoofed_forwarded_for_does_not_bypass_limit(
    make_client: Callable[..., TestClient], clock: FakeClock, psp: FakePsp
) -> None:
    untrusted = make_client(rate_limit_login="5/minute")
    behind_proxy = make_client(rate_limit_login="5/minute", client_address=("127.0.0.1", 40000))
    body = {"email": "victim@example.org", "password": "guess-password-x"}

    spoofed = [
        untrusted.post("/api/auth/login", json=body, headers={"X-Forwarded-For": f"203.0.113.{i}"})
        for i in range(6)
    ]
    forwarded = [
        behind_proxy.post(
            "/api/auth/login", json=body, headers={"X-Forwarded-For": f"198.51.100.{i}"}
        )
        for i in range(6)
    ]

    assert [r.status_code for r in spoofed] == [401] * 5 + [429]
    assert [r.status_code for r in forwarded] == [401] * 6


def test_as01_account_locked_after_ten_failures(api: Api, captured_logs: io.StringIO) -> None:
    player = api.signup()

    failures = [api.login(player.email, f"wrong-password-{i}") for i in range(10)]
    while_locked = api.login(player.email, DEFAULT_PASSWORD)
    api.clock.advance(timedelta(minutes=15, seconds=1))
    after_lock = api.login(player.email, DEFAULT_PASSWORD)

    assert {r.status_code for r in failures} == {401}
    assert while_locked.status_code == 401
    assert _body_without_request_id(while_locked) == _body_without_request_id(failures[0])
    assert after_lock.status_code == 200
    assert captured_logs.getvalue().count("auth.account_locked") == 1


def _claims(api: Api, token: str) -> dict[str, Any]:
    secret = api.settings.jwt_secret.get_secret_value()
    claims: dict[str, Any] = jwt.decode(token, secret, algorithms=["HS256"], audience="toutbet-api")
    return claims


TAMPERINGS: dict[str, Callable[[dict[str, Any], str], str]] = {
    "alg_none": lambda claims, secret: jwt.encode(claims, None, algorithm="none"),
    "bad_signature": lambda claims, secret: jwt.encode(claims, "x" * 48, algorithm="HS256"),
    "hs512": lambda claims, secret: jwt.encode(claims, secret, algorithm="HS512"),
    "expired": lambda claims, secret: jwt.encode(
        {**claims, "iat": claims["iat"] - 2000, "exp": claims["iat"] - 1000}, secret, "HS256"
    ),
    "wrong_audience": lambda claims, secret: jwt.encode(
        {**claims, "aud": "another-api"}, secret, algorithm="HS256"
    ),
    "missing_sid": lambda claims, secret: jwt.encode(
        {k: v for k, v in claims.items() if k != "sid"}, secret, algorithm="HS256"
    ),
}


@pytest.mark.parametrize("tampering", sorted(TAMPERINGS))
def test_as01_tampered_token_rejected(api: Api, client: TestClient, tampering: str) -> None:
    player = api.signup()
    secret = api.settings.jwt_secret.get_secret_value()

    forged = TAMPERINGS[tampering](_claims(api, player.token), secret)
    response = client.get("/api/me", headers={"Authorization": f"Bearer {forged}"})

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "UNAUTHENTICATED"
    assert client.get("/api/me", headers=player.headers).status_code == 200


def test_as01_login_error_identical_for_unknown_email(api: Api, argon2_calls: list[str]) -> None:
    player = api.signup()
    argon2_calls.clear()

    known = api.login(player.email, "not the right password")
    known_calls = list(argon2_calls)
    argon2_calls.clear()
    unknown = api.login("ghost@example.org", "not the right password")

    assert known.status_code == unknown.status_code == 401
    assert _body_without_request_id(known) == _body_without_request_id(unknown)
    assert known_calls == argon2_calls == ["verify"]


def test_as01_register_response_identical_for_existing_email(
    api: Api, argon2_calls: list[str]
) -> None:
    api.register(email="taken@example.org", display_name="taken_1")
    argon2_calls.clear()

    duplicate = api.register(email="taken@example.org", display_name="taken_2")
    duplicate_calls = list(argon2_calls)
    argon2_calls.clear()
    fresh = api.register(email="fresh@example.org", display_name="fresh_1")

    assert duplicate.status_code == fresh.status_code == 202
    assert duplicate.json() == fresh.json()
    assert duplicate_calls == argon2_calls == ["hash"]


def test_as01_long_password_rejected_without_hashing(api: Api, argon2_calls: list[str]) -> None:
    giant = "p" * 129

    register = api.register(password=giant)
    login = api.login("someone@example.org", giant)

    assert register.status_code == login.status_code == 422
    assert argon2_calls == []
    assert api.register(password="p" * 128).status_code == 202


def test_as01_rate_limit_applies_before_hashing(
    make_client: Callable[..., TestClient],
    clock: FakeClock,
    psp: FakePsp,
    argon2_calls: list[str],
) -> None:
    client = make_client(rate_limit_login="5/minute")
    statements: list[str] = []
    engine = client.app.state.engine
    body = {"email": "victim@example.org", "password": "guess-password-x"}
    for _ in range(5):
        client.post("/api/auth/login", json=body)
    argon2_calls.clear()

    def record(*args: Any) -> None:
        statements.append(str(args[2]))

    event.listen(engine, "before_cursor_execute", record)
    try:
        blocked = client.post("/api/auth/login", json=body)
    finally:
        event.remove(engine, "before_cursor_execute", record)

    assert blocked.status_code == 429
    assert argon2_calls == []
    assert statements == []
