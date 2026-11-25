import uuid
from datetime import datetime, timedelta
from typing import Any, cast

from sqlalchemy import CursorResult, update
from sqlalchemy.orm import Session

from toutbet.models import LoginSession

SESSION_TTL = timedelta(hours=8)


def open_session(db: Session, user_id: uuid.UUID, now: datetime) -> LoginSession:
    login_session = LoginSession(
        id=uuid.uuid4(),
        user_id=user_id,
        current_jti=uuid.uuid4(),
        created_at=now,
        expires_at=now + SESSION_TTL,
        revoked_at=None,
    )
    db.add(login_session)
    db.flush()
    return login_session


def is_session_active(login_session: LoginSession, jti: uuid.UUID, now: datetime) -> bool:
    return (
        login_session.revoked_at is None
        and login_session.expires_at > now
        and login_session.current_jti == jti
    )


def rotate_jti(
    db: Session, session_id: uuid.UUID, current_jti: uuid.UUID, now: datetime
) -> uuid.UUID | None:
    new_jti = uuid.uuid4()
    statement = (
        update(LoginSession)
        .where(
            LoginSession.id == session_id,
            LoginSession.current_jti == current_jti,
            LoginSession.revoked_at.is_(None),
            LoginSession.expires_at > now,
        )
        .values(current_jti=new_jti)
        .execution_options(synchronize_session=False)
    )
    result = cast(CursorResult[Any], db.execute(statement))
    return new_jti if result.rowcount == 1 else None


def revoke_session(db: Session, session_id: uuid.UUID, now: datetime) -> None:
    statement = (
        update(LoginSession)
        .where(LoginSession.id == session_id, LoginSession.revoked_at.is_(None))
        .values(revoked_at=now)
        .execution_options(synchronize_session=False)
    )
    db.execute(statement)


def revoke_all_sessions(db: Session, user_id: uuid.UUID, now: datetime) -> int:
    statement = (
        update(LoginSession)
        .where(LoginSession.user_id == user_id, LoginSession.revoked_at.is_(None))
        .values(revoked_at=now)
        .execution_options(synchronize_session=False)
    )
    result = cast(CursorResult[Any], db.execute(statement))
    return result.rowcount
