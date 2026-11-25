import logging
import uuid

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from toutbet.api.deps import (
    ClockDep,
    IdempotencyKey,
    PspDep,
    SettingsDep,
    protected_router,
    require_feature,
)
from toutbet.db import DbSession
from toutbet.errors import ApiError, ErrorCode
from toutbet.observability import log_event
from toutbet.psp.client import PspRefusedError, PspUnavailableError
from toutbet.rate_limit import RateLimits
from toutbet.schemas.wallet import DepositCreate, DepositCreated, DepositView
from toutbet.security.authz import AuthenticatedUser, Player
from toutbet.services.deposits import (
    CURRENCY,
    attach_checkout,
    fail_deposit,
    find_deposit_for,
    open_deposit,
)
from toutbet.services.idempotency import (
    claim,
    complete,
    release,
    replay_response,
    request_fingerprint,
)

ENDPOINT = "POST /api/deposits"


def build_router(limits: RateLimits) -> APIRouter:
    router = protected_router(prefix="/api/deposits", tags=["deposits"])

    @router.post(
        "",
        status_code=201,
        response_model=DepositCreated,
        dependencies=[Depends(require_feature("deposits_enabled"))],
    )
    @limits.writes()
    def create_deposit(
        request: Request,
        body: DepositCreate,
        user: Player,
        db: DbSession,
        clock: ClockDep,
        psp: PspDep,
        settings: SettingsDep,
        idempotency_key: IdempotencyKey,
    ) -> JSONResponse:
        key = str(idempotency_key)
        fingerprint = request_fingerprint("POST", request.url.path, body.model_dump())
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
            deposit = open_deposit(db, user=user, amount_cents=body.amount_cents, now=clock.now())
        try:
            checkout = psp.create_checkout(
                reference=str(deposit.id),
                amount_cents=deposit.amount_cents,
                currency=CURRENCY,
                return_url=f"{settings.public_base_url}/#/wallet",
            )
        except (PspUnavailableError, PspRefusedError) as error:
            with db.begin():
                fail_deposit(db, deposit.id)
                release(db, user_id=user.id, key=key)
            log_event(
                "psp.timeout",
                level=logging.WARNING,
                user_id=str(user.id),
                deposit_id=str(deposit.id),
                cause=type(error).__name__,
            )
            raise ApiError(503, ErrorCode.PSP_UNAVAILABLE) from None
        payload = DepositCreated(
            deposit_id=deposit.id, status=deposit.status, redirect_url=checkout.redirect_url
        ).model_dump(mode="json")
        with db.begin():
            attach_checkout(db, deposit.id, checkout.session_id)
            complete(db, user_id=user.id, key=key, status_code=201, body=payload)
        return JSONResponse(payload, status_code=201)

    @router.get("/{deposit_id}", response_model=DepositView)
    @limits.reads()
    def read_deposit(
        request: Request, deposit_id: uuid.UUID, user: AuthenticatedUser, db: DbSession
    ) -> DepositView:
        with db.begin():
            deposit = find_deposit_for(db, user, deposit_id)
        return DepositView(
            id=deposit.id,
            amount_cents=deposit.amount_cents,
            currency=deposit.currency,
            status=deposit.status,
            created_at=deposit.created_at,
        )

    return router
