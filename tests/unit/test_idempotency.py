import uuid
from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from toutbet.errors import ApiError
from toutbet.models import IdempotencyRecord
from toutbet.services.accounts import create_user
from toutbet.services.idempotency import (
    StoredResponse,
    claim,
    complete,
    purge_expired,
    release,
    request_fingerprint,
)

NOW = datetime(2026, 11, 25, 12, 0, tzinfo=UTC)
ENDPOINT = "POST /api/bets/{bet_id}/stakes"


@pytest.fixture
def user_id(db_session: Session) -> uuid.UUID:
    with db_session.begin():
        user = create_user(
            db_session,
            email="idem@example.org",
            display_name="idem_user",
            password_hash="$argon2id$placeholder",
            birth_date=date(1990, 1, 1),
            role="user",
            now=NOW,
        )
    return user.id


def _claim(db: Session, user_id: uuid.UUID, key: str, fingerprint: str) -> StoredResponse | None:
    return claim(db, user_id=user_id, key=key, endpoint=ENDPOINT, request_hash=fingerprint, now=NOW)


def test_fingerprint_depends_on_path_and_body() -> None:
    base = request_fingerprint("POST", "/api/bets/a/stakes", {"amount_cents": 100})

    assert base == request_fingerprint("POST", "/api/bets/a/stakes", {"amount_cents": 100})
    assert base != request_fingerprint("POST", "/api/bets/b/stakes", {"amount_cents": 100})
    assert base != request_fingerprint("POST", "/api/bets/a/stakes", {"amount_cents": 101})


def test_first_claim_runs_then_replays_stored_response(
    db_session: Session, user_id: uuid.UUID
) -> None:
    with db_session.begin():
        assert _claim(db_session, user_id, "k1", "h1") is None
        complete(db_session, user_id=user_id, key="k1", status_code=201, body={"ok": True})

    with db_session.begin():
        stored = _claim(db_session, user_id, "k1", "h1")

    assert stored is not None
    assert stored.status_code == 201
    assert stored.body == '{"ok":true}'


def test_same_key_with_other_request_is_rejected(db_session: Session, user_id: uuid.UUID) -> None:
    with db_session.begin():
        _claim(db_session, user_id, "k1", "h1")
        complete(db_session, user_id=user_id, key="k1", status_code=201, body={})

    with pytest.raises(ApiError) as error, db_session.begin():
        _claim(db_session, user_id, "k1", "h2")

    assert error.value.status_code == 422
    assert error.value.code == "IDEMPOTENCY_KEY_REUSED"


def test_claim_in_progress_is_reported(db_session: Session, user_id: uuid.UUID) -> None:
    with db_session.begin():
        _claim(db_session, user_id, "k1", "h1")

    with pytest.raises(ApiError) as error, db_session.begin():
        _claim(db_session, user_id, "k1", "h1")

    assert error.value.status_code == 409
    assert error.value.code == "IDEMPOTENCY_IN_PROGRESS"


def test_rolled_back_operation_leaves_no_claim(db_session: Session, user_id: uuid.UUID) -> None:
    def failing_operation() -> None:
        with db_session.begin():
            _claim(db_session, user_id, "k1", "h1")
            raise RuntimeError("operation failed")

    with pytest.raises(RuntimeError):
        failing_operation()

    with db_session.begin():
        assert db_session.scalars(select(IdempotencyRecord)).all() == []
        assert _claim(db_session, user_id, "k1", "h1") is None
        release(db_session, user_id=user_id, key="k1")
        assert db_session.scalars(select(IdempotencyRecord)).all() == []


def test_keys_are_purged_after_24_hours(db_session: Session, user_id: uuid.UUID) -> None:
    with db_session.begin():
        _claim(db_session, user_id, "old", "h1")

    with db_session.begin():
        purge_expired(db_session, NOW + timedelta(hours=23))
        assert db_session.scalar(select(func.count()).select_from(IdempotencyRecord)) == 1
        purge_expired(db_session, NOW + timedelta(hours=24, seconds=1))
        assert db_session.scalars(select(IdempotencyRecord)).all() == []
