import logging

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from toutbet.api.deps import (
    ClockDep,
    IdempotencyKey,
    PspDep,
    protected_router,
    require_feature,
    trace_for,
)
from toutbet.db import DbSession
from toutbet.errors import ApiError, ErrorCode
from toutbet.models.payment import WITHDRAWAL_SENT
from toutbet.models.user import KYC_VERIFIED
from toutbet.observability import log_event
from toutbet.psp.client import PspRefusedError, PspUnavailableError
from toutbet.rate_limit import RateLimits
from toutbet.schemas.wallet import WithdrawalCreate, WithdrawalView
from toutbet.security.authz import Player
from toutbet.services.deposits import CURRENCY
from toutbet.services.idempotency import (
    claim,
    complete,
    release,
    replay_response,
    request_fingerprint,
)
from toutbet.services.withdrawals import (
    mark_sent,
    open_withdrawal,
    password_matches,
    reverse_withdrawal,
)

ENDPOINT = "POST /api/withdrawals"
REVERSED_MESSAGE = "Le virement n'a pas pu être effectué, votre solde a été recrédité."


def build_router(limits: RateLimits) -> APIRouter:
    router = protected_router(prefix="/api/withdrawals", tags=["withdrawals"])

    @router.post(
        "",
        status_code=201,
        response_model=WithdrawalView,
        dependencies=[Depends(require_feature("withdrawals_enabled"))],
    )
    @limits.per_user(limits.settings.rate_limit_withdrawals)
    def create_withdrawal(
        request: Request,
        body: WithdrawalCreate,
        user: Player,
        db: DbSession,
        clock: ClockDep,
        psp: PspDep,
        idempotency_key: IdempotencyKey,
    ) -> JSONResponse:
        if not password_matches(db, user.id, body.password):
            log_event("auth.reauthentication_failed", user_id=str(user.id))
            raise ApiError(401, ErrorCode.UNAUTHENTICATED)
        if user.kyc_status != KYC_VERIFIED:
            raise ApiError(403, ErrorCode.KYC_REQUIRED)
        key = str(idempotency_key)
        trace = trace_for(request, user, key)
        fingerprint = request_fingerprint(
            "POST", request.url.path, {"amount_cents": body.amount_cents}
        )
        with db.begin():
            replay = claim(
                db,
                user_id=user.id,
                key=key,
                endpoint=ENDPOINT,
                request_hash=fingerprint,
                now=clock.now(),
            )
            if replay is not None:
                return replay_response(replay)
            withdrawal = open_withdrawal(
                db, user=user, amount_cents=body.amount_cents, now=clock.now(), trace=trace
            )
        try:
            payout = psp.create_payout(
                reference=str(withdrawal.id),
                user_reference=str(user.id),
                amount_cents=withdrawal.amount_cents,
                currency=CURRENCY,
            )
        except PspRefusedError:
            with db.begin():
                reverse_withdrawal(
                    db, withdrawal=withdrawal, now=clock.now(), trace=trace, reason="psp_refused"
                )
                release(db, user_id=user.id, key=key)
            raise ApiError(503, ErrorCode.PSP_UNAVAILABLE, REVERSED_MESSAGE) from None
        except PspUnavailableError as error:
            log_event(
                "money.withdrawal_unconfirmed",
                level=logging.WARNING,
                user_id=str(user.id),
                withdrawal_id=str(withdrawal.id),
                cause=str(error),
            )
            raise ApiError(503, ErrorCode.WITHDRAWAL_PENDING) from None
        payload = WithdrawalView(
            id=withdrawal.id, amount_cents=withdrawal.amount_cents, status=WITHDRAWAL_SENT
        ).model_dump(mode="json")
        with db.begin():
            mark_sent(db, withdrawal.id, payout.payout_id)
            complete(db, user_id=user.id, key=key, status_code=201, body=payload)
        return JSONResponse(payload, status_code=201)

    return router
