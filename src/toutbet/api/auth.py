import uuid
from datetime import datetime

from fastapi import APIRouter, Request, Response
from sqlalchemy import select

from toutbet.api.deps import ClockDep, SettingsDep, protected_router
from toutbet.config import Settings
from toutbet.db import DbSession
from toutbet.errors import ApiError, ErrorCode
from toutbet.models import User
from toutbet.rate_limit import RateLimits
from toutbet.schemas.auth import LoginRequest, MeResponse, RegisterRequest, TokenResponse
from toutbet.schemas.common import StatusResponse
from toutbet.security.authz import AuthenticatedUser
from toutbet.security.sessions import revoke_session, rotate_jti
from toutbet.security.tokens import ACCESS_TOKEN_TTL_SECONDS, issue_access_token
from toutbet.services.accounts import authenticate, register


def _token_response(
    settings: Settings, user_id: uuid.UUID, session_id: uuid.UUID, jti: uuid.UUID, now: datetime
) -> TokenResponse:
    token = issue_access_token(
        settings.jwt_secret.get_secret_value(), user_id, session_id, jti, now
    )
    return TokenResponse(access_token=token, expires_in=ACCESS_TOKEN_TTL_SECONDS)


def build_public_router(limits: RateLimits) -> APIRouter:
    router = APIRouter(prefix="/api/auth", tags=["auth"])

    @router.post("/register", status_code=202, response_model=StatusResponse)
    @limits.per_ip(limits.settings.rate_limit_register)
    def register_account(
        request: Request, body: RegisterRequest, db: DbSession, clock: ClockDep
    ) -> StatusResponse:
        register(db, body, clock.now())
        return StatusResponse(status="accepted")

    @router.post("/login", response_model=TokenResponse)
    @limits.per_ip(limits.settings.rate_limit_login)
    def login(
        request: Request,
        body: LoginRequest,
        db: DbSession,
        clock: ClockDep,
        settings: SettingsDep,
    ) -> TokenResponse:
        now = clock.now()
        login_session = authenticate(db, body.email, body.password, now)
        if login_session is None:
            raise ApiError(401, ErrorCode.UNAUTHENTICATED)
        return _token_response(
            settings, login_session.user_id, login_session.id, login_session.current_jti, now
        )

    return router


def build_router(limits: RateLimits) -> APIRouter:
    router = protected_router(prefix="/api", tags=["auth"])

    @router.post("/auth/refresh", response_model=TokenResponse)
    @limits.per_user(limits.settings.rate_limit_refresh)
    def refresh(
        request: Request,
        user: AuthenticatedUser,
        db: DbSession,
        clock: ClockDep,
        settings: SettingsDep,
    ) -> TokenResponse:
        now = clock.now()
        with db.begin():
            new_jti = rotate_jti(db, user.session_id, user.jti, now)
        if new_jti is None:
            raise ApiError(401, ErrorCode.UNAUTHENTICATED)
        return _token_response(settings, user.id, user.session_id, new_jti, now)

    @router.post("/auth/logout", status_code=204)
    @limits.writes()
    def logout(
        request: Request, user: AuthenticatedUser, db: DbSession, clock: ClockDep
    ) -> Response:
        with db.begin():
            revoke_session(db, user.session_id, clock.now())
        return Response(status_code=204)

    @router.get("/me", response_model=MeResponse)
    @limits.reads()
    def me(
        request: Request, user: AuthenticatedUser, db: DbSession, settings: SettingsDep
    ) -> MeResponse:
        with db.begin():
            email = db.execute(select(User.email).where(User.id == user.id)).scalar_one()
        return MeResponse(
            id=user.id,
            email=email,
            display_name=user.display_name,
            role=user.role,
            kyc_status=user.kyc_status,
            bet_creation_enabled=settings.bet_creation_enabled,
        )

    return router
