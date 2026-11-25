import uuid
from datetime import date, datetime, timedelta

from sqlalchemy import case, func, literal, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from toutbet.errors import ApiError, ErrorCode
from toutbet.models import LoginSession, User
from toutbet.models.base import UtcDateTime
from toutbet.models.user import KYC_NONE, ROLE_USER
from toutbet.observability import log_event
from toutbet.schemas.auth import RegisterRequest
from toutbet.security.passwords import (
    hash_password,
    needs_rehash,
    spend_equivalent_verification,
    verify_password,
)
from toutbet.security.sessions import open_session
from toutbet.services.money import open_wallet

ADULT_AGE_YEARS = 18
LOCK_THRESHOLD = 10
LOCK_WINDOW = timedelta(minutes=15)
LOCK_DURATION = timedelta(minutes=15)


def is_adult(birth_date: date, today: date) -> bool:
    had_birthday = (today.month, today.day) >= (birth_date.month, birth_date.day)
    age = today.year - birth_date.year - (0 if had_birthday else 1)
    return age >= ADULT_AGE_YEARS


def create_user(
    db: Session,
    *,
    email: str,
    display_name: str,
    password_hash: str,
    birth_date: date,
    role: str,
    now: datetime,
) -> User:
    user = User(
        id=uuid.uuid4(),
        email=email,
        password_hash=password_hash,
        display_name=display_name,
        birth_date=birth_date,
        role=role,
        kyc_status=KYC_NONE,
        failed_login_count=0,
        created_at=now,
    )
    db.add(user)
    db.flush()
    open_wallet(db, user.id)
    db.flush()
    return user


def register(db: Session, data: RegisterRequest, now: datetime) -> None:
    if not is_adult(data.birth_date, now.date()):
        raise ApiError(422, ErrorCode.VALIDATION_ERROR, "Vous devez avoir 18 ans révolus.")
    password_hash = hash_password(data.password)
    try:
        with db.begin():
            taken = db.scalar(
                select(User.id).where(
                    or_(
                        User.email == data.email,
                        func.lower(User.display_name) == data.display_name.lower(),
                    )
                )
            )
            if taken is not None:
                log_event("auth.register_conflict")
                return
            user = create_user(
                db,
                email=data.email,
                display_name=data.display_name,
                password_hash=password_hash,
                birth_date=data.birth_date,
                role=ROLE_USER,
                now=now,
            )
            log_event("auth.registered", user_id=str(user.id))
    except IntegrityError:
        log_event("auth.register_conflict")


def authenticate(db: Session, email: str, password: str, now: datetime) -> LoginSession | None:
    with db.begin():
        user = db.scalar(select(User).where(User.email == email))
    if user is None:
        spend_equivalent_verification(password)
        log_event("auth.login_failed", reason="unknown_account")
        return None
    if user.locked_until is not None and user.locked_until > now:
        spend_equivalent_verification(password)
        log_event("auth.login_failed", user_id=str(user.id), reason="locked")
        return None
    if not verify_password(user.password_hash, password):
        with db.begin():
            _record_failure(db, user.id, now)
        log_event("auth.login_failed", user_id=str(user.id), reason="bad_password")
        return None
    with db.begin():
        values: dict[str, object] = {
            "failed_login_count": 0,
            "failed_login_window_started_at": None,
            "locked_until": None,
        }
        if needs_rehash(user.password_hash):
            values["password_hash"] = hash_password(password)
        db.execute(
            update(User)
            .where(User.id == user.id)
            .values(**values)
            .execution_options(synchronize_session=False)
        )
        login_session = open_session(db, user.id, now)
    log_event("auth.login_succeeded", user_id=str(user.id))
    return login_session


def _record_failure(db: Session, user_id: uuid.UUID, now: datetime) -> None:
    window_expired = or_(
        User.failed_login_window_started_at.is_(None),
        User.failed_login_window_started_at <= now - LOCK_WINDOW,
    )
    failures = db.execute(
        update(User)
        .where(User.id == user_id)
        .values(
            failed_login_count=case((window_expired, 1), else_=User.failed_login_count + 1),
            failed_login_window_started_at=case(
                (window_expired, literal(now, UtcDateTime())),
                else_=User.failed_login_window_started_at,
            ),
        )
        .returning(User.failed_login_count)
        .execution_options(synchronize_session=False)
    ).scalar_one()
    if failures >= LOCK_THRESHOLD:
        db.execute(
            update(User)
            .where(User.id == user_id)
            .values(
                locked_until=now + LOCK_DURATION,
                failed_login_count=0,
                failed_login_window_started_at=None,
            )
            .execution_options(synchronize_session=False)
        )
        log_event("auth.account_locked", user_id=str(user_id))
