from typing import Annotated

from fastapi import APIRouter, Depends, Request

from toutbet.api.deps import get_receipt_signer
from toutbet.rate_limit import RateLimits
from toutbet.schemas.bets import PublicKeyResponse
from toutbet.security.receipts import ReceiptSigner


def build_router(limits: RateLimits) -> APIRouter:
    router = APIRouter(prefix="/api/receipts", tags=["receipts"])

    @router.get("/public-key", response_model=PublicKeyResponse)
    @limits.per_ip(limits.settings.rate_limit_read)
    def public_key(
        request: Request, signer: Annotated[ReceiptSigner, Depends(get_receipt_signer)]
    ) -> PublicKeyResponse:
        return PublicKeyResponse(
            key_id=signer.key_id, algorithm="Ed25519", public_key=signer.public_key
        )

    return router
