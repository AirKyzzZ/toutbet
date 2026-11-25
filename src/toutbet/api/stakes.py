import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from toutbet.api.deps import (
    ClockDep,
    IdempotencyKey,
    get_receipt_signer,
    protected_router,
    require_feature,
    trace_for,
)
from toutbet.db import DbSession
from toutbet.rate_limit import RateLimits
from toutbet.schemas.bets import MyStake, Receipt, StakeCreate, StakeResponse
from toutbet.security.authz import Player
from toutbet.security.receipts import ReceiptSigner
from toutbet.services.idempotency import claim, complete, replay_response, request_fingerprint
from toutbet.services.stakes import place_stake

ENDPOINT = "POST /api/bets/{bet_id}/stakes"


def build_router(limits: RateLimits) -> APIRouter:
    router = protected_router(prefix="/api/bets", tags=["stakes"])

    @router.post(
        "/{bet_id}/stakes",
        status_code=201,
        response_model=StakeResponse,
        dependencies=[Depends(require_feature("stakes_enabled"))],
    )
    @limits.writes()
    def stake(
        request: Request,
        bet_id: uuid.UUID,
        body: StakeCreate,
        user: Player,
        db: DbSession,
        clock: ClockDep,
        signer: Annotated[ReceiptSigner, Depends(get_receipt_signer)],
        idempotency_key: IdempotencyKey,
    ) -> JSONResponse:
        key = str(idempotency_key)
        fingerprint = request_fingerprint("POST", request.url.path, body.model_dump())
        with db.begin():
            now = clock.now()
            replay = claim(
                db, user_id=user.id, key=key, endpoint=ENDPOINT, request_hash=fingerprint, now=now
            )
            if replay is not None:
                return replay_response(replay)
            placed = place_stake(
                db,
                user=user,
                bet_id=bet_id,
                outcome=body.outcome,
                amount_cents=body.amount_cents,
                now=now,
                trace=trace_for(request, user, key),
                signer=signer,
            )
            payload = StakeResponse(
                stake=MyStake(
                    id=placed.stake.id,
                    outcome=placed.stake.outcome,
                    amount_cents=placed.stake.amount_cents,
                    created_at=placed.stake.created_at,
                ),
                bet_id=bet_id,
                ledger_hash=placed.stake.ledger_hash,
                receipt=Receipt(
                    payload=placed.receipt.payload,
                    signature=placed.receipt.signature,
                    key_id=placed.receipt.key_id,
                ),
            ).model_dump(mode="json")
            complete(db, user_id=user.id, key=key, status_code=201, body=payload)
        return JSONResponse(payload, status_code=201)

    return router
