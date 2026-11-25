from datetime import timedelta

import pytest
from argon2 import PasswordHasher
from fastapi.testclient import TestClient
from sqlalchemy import select

from support import DEFAULT_PASSWORD, Api
from toutbet.models import LoginSession, User, Wallet
from toutbet.security.tokens import decode_access_token


def test_us01_1_registration_creates_user_wallet_and_argon2id_hash(api: Api) -> None:
    response = api.register(email="Alice@Example.org", display_name="alice_1")

    assert response.status_code == 202
    assert response.json() == {"status": "accepted"}
    with api.db() as db:
        user = db.execute(select(User).where(User.email == "alice@example.org")).scalar_one()
        wallet = db.get(Wallet, user.id)
        assert user.role == "user"
        assert user.password_hash.startswith("$argon2id$")
        assert DEFAULT_PASSWORD not in user.password_hash
        assert PasswordHasher().verify(user.password_hash, DEFAULT_PASSWORD)
        assert wallet is not None
        assert wallet.balance_cents == 0


def test_us01_1_minors_and_invalid_identities_are_refused(api: Api) -> None:
    today = api.clock.now().date()
    minor = today.replace(year=today.year - 18) + timedelta(days=1)

    assert api.register(birth_date=minor.isoformat()).status_code == 422
    assert api.register(display_name="a b").status_code == 422
    assert api.register(display_name="ab").status_code == 422
    assert api.register(password="short").status_code == 422
    assert api.register(email="not-an-email").status_code == 422
    with api.db() as db:
        assert db.scalars(select(User)).all() == []


def test_us01_2_duplicate_email_gets_identical_response_and_same_hash_work(
    api: Api, monkeypatch: pytest.MonkeyPatch
) -> None:
    first = api.register(email="bob@example.org", display_name="bob_1")
    calls: list[str] = []
    original = PasswordHasher.hash

    def spy(self: PasswordHasher, password: str | bytes, *, salt: bytes | None = None) -> str:
        calls.append("hash")
        return original(self, password, salt=salt)

    monkeypatch.setattr(PasswordHasher, "hash", spy)
    second = api.register(email="BOB@example.org", display_name="bob_2")

    assert first.status_code == second.status_code == 202
    assert first.json() == second.json()
    assert calls == ["hash"]
    with api.db() as db:
        assert len(db.scalars(select(User)).all()) == 1


def test_us01_3_login_opens_server_session_and_returns_15_minute_jwt(api: Api) -> None:
    player = api.signup()

    response = api.login(player.email)

    assert response.status_code == 200
    body = response.json()
    assert body["token_type"] == "bearer"
    assert body["expires_in"] == 900
    claims = decode_access_token(
        body["access_token"], api.settings.jwt_secret.get_secret_value(), api.clock.now()
    )
    with api.db() as db:
        login_session = db.get(LoginSession, claims.session_id)
        assert login_session is not None
        assert login_session.current_jti == claims.jti
        assert login_session.expires_at - login_session.created_at == timedelta(hours=8)


def test_us01_4_invalid_credentials_get_same_401(api: Api) -> None:
    player = api.signup()

    wrong_password = api.login(player.email, "not the right password")
    unknown_email = api.login("nobody@example.org", "not the right password")

    assert wrong_password.status_code == unknown_email.status_code == 401
    assert wrong_password.json()["error"]["message"] == unknown_email.json()["error"]["message"]
    assert wrong_password.json()["error"]["code"] == "UNAUTHENTICATED"


def test_us01_5_token_rejected_after_logout(api: Api, client: TestClient) -> None:
    player = api.signup()

    assert client.post("/api/auth/logout", headers=player.headers).status_code == 204

    response = client.get("/api/me", headers=player.headers)
    assert response.status_code == 401
    with api.db() as db:
        revoked = db.scalars(select(LoginSession.revoked_at)).all()
        assert all(moment is not None for moment in revoked)


def test_us01_6_old_token_rejected_after_refresh(api: Api, client: TestClient) -> None:
    player = api.signup()
    old_headers = player.headers

    refreshed = client.post("/api/auth/refresh", headers=old_headers)

    assert refreshed.status_code == 200
    new_headers = {"Authorization": f"Bearer {refreshed.json()['access_token']}"}
    assert client.get("/api/me", headers=old_headers).status_code == 401
    assert client.get("/api/me", headers=new_headers).status_code == 200


def test_us01_6_refresh_refused_once_session_is_8_hours_old(api: Api, client: TestClient) -> None:
    player = api.signup()
    opened_at = api.clock.now()
    statuses = []
    for _ in range(36):
        api.clock.advance(timedelta(minutes=14))
        response = client.post("/api/auth/refresh", headers=player.headers)
        statuses.append((api.clock.now() - opened_at, response.status_code))
        if response.status_code != 200:
            break
        player.token = response.json()["access_token"]

    assert all(code == 200 for elapsed, code in statuses if elapsed < timedelta(hours=8))
    assert statuses[-1][1] == 401
    assert statuses[-1][0] >= timedelta(hours=8)
