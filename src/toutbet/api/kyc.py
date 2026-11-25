import logging

from fastapi import APIRouter, Request
from sqlalchemy import update

from toutbet.api.deps import PspDep, SettingsDep, protected_router
from toutbet.db import DbSession
from toutbet.errors import ApiError, ErrorCode
from toutbet.models import User
from toutbet.models.user import KYC_NONE, KYC_PENDING, KYC_REJECTED, KYC_VERIFIED
from toutbet.observability import log_event
from toutbet.psp.client import PspRefusedError, PspUnavailableError
from toutbet.rate_limit import RateLimits
from toutbet.schemas.wallet import RedirectResponse
from toutbet.security.authz import Player


def build_router(limits: RateLimits) -> APIRouter:
    router = protected_router(prefix="/api/kyc", tags=["kyc"])

    @router.post("/start", response_model=RedirectResponse)
    @limits.per_user(limits.settings.rate_limit_kyc)
    def start_kyc(
        request: Request, user: Player, db: DbSession, psp: PspDep, settings: SettingsDep
    ) -> RedirectResponse:
        if user.kyc_status == KYC_VERIFIED:
            raise ApiError(409, ErrorCode.INVALID_STATE, "Votre identité est déjà vérifiée.")
        try:
            hosted = psp.create_kyc_session(
                reference=str(user.id), return_url=f"{settings.public_base_url}/#/wallet"
            )
        except (PspUnavailableError, PspRefusedError) as error:
            log_event(
                "psp.timeout",
                level=logging.WARNING,
                user_id=str(user.id),
                cause=type(error).__name__,
            )
            raise ApiError(503, ErrorCode.PSP_UNAVAILABLE) from None
        with db.begin():
            db.execute(
                update(User)
                .where(User.id == user.id, User.kyc_status.in_([KYC_NONE, KYC_REJECTED]))
                .values(kyc_status=KYC_PENDING)
                .execution_options(synchronize_session=False)
            )
        return RedirectResponse(redirect_url=hosted.redirect_url)

    return router
