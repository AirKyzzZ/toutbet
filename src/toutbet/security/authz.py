import uuid
from dataclasses import dataclass
from typing import Annotated, NoReturn

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select

from toutbet.clock import Clock, get_clock
from toutbet.config import Settings, get_settings
from toutbet.db import DbSession
from toutbet.errors import ApiError, ErrorCode
from toutbet.models import LoginSession, User
from toutbet.models.user import ROLE_MODERATOR, ROLE_USER
from toutbet.observability import log_event
from toutbet.security.sessions import is_session_active
from toutbet.security.tokens import InvalidTokenError, decode_access_token

_bearer = HTTPBearer(auto_error=False)


@dataclass(frozen=True)
class CurrentUser:
    id: uuid.UUID
    session_id: uuid.UUID
    jti: uuid.UUID
    role: str
    kyc_status: str
    display_name: str


def _reject(reason: str) -> NoReturn:
    log_event("auth.token_rejected", reason=reason)
    raise ApiError(401, ErrorCode.UNAUTHENTICATED, headers={"WWW-Authenticate": "Bearer"})


def current_user(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
    db: DbSession,
    clock: Annotated[Clock, Depends(get_clock)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> CurrentUser:
    if credentials is None or credentials.scheme.lower() != "bearer":
        _reject("missing")
    now = clock.now()
    try:
        claims = decode_access_token(
            credentials.credentials, settings.jwt_secret.get_secret_value(), now
        )
    except InvalidTokenError:
        _reject("invalid")
    with db.begin():
        row = db.execute(
            select(LoginSession, User)
            .join(User, User.id == LoginSession.user_id)
            .where(LoginSession.id == claims.session_id)
        ).one_or_none()
    if row is None:
        _reject("unknown_session")
    login_session, user = row
    if user.id != claims.user_id or not is_session_active(login_session, claims.jti, now):
        _reject("inactive_session")
    request.state.user_id = str(user.id)
    return CurrentUser(
        id=user.id,
        session_id=login_session.id,
        jti=claims.jti,
        role=user.role,
        kyc_status=user.kyc_status,
        display_name=user.display_name,
    )


AuthenticatedUser = Annotated[CurrentUser, Depends(current_user)]


def deny(user_id: uuid.UUID, reason: str) -> NoReturn:
    log_event("authz.denied", user_id=str(user_id), reason=reason)
    raise ApiError(403, ErrorCode.FORBIDDEN)


def require_moderator(user: AuthenticatedUser) -> CurrentUser:
    if user.role != ROLE_MODERATOR:
        deny(user.id, "moderator_required")
    return user


def require_player(user: AuthenticatedUser) -> CurrentUser:
    if user.role != ROLE_USER:
        deny(user.id, "player_required")
    return user


Moderator = Annotated[CurrentUser, Depends(require_moderator)]
Player = Annotated[CurrentUser, Depends(require_player)]
