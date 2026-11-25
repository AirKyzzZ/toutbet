import uuid
from collections.abc import Callable
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Header, Request

from toutbet.clock import Clock, get_clock
from toutbet.config import Settings, get_settings
from toutbet.errors import ApiError, ErrorCode, request_id_of
from toutbet.psp.client import PspClient, get_psp_client
from toutbet.security.authz import CurrentUser, current_user
from toutbet.security.receipts import ReceiptSigner
from toutbet.services.ledger import Trace

ClockDep = Annotated[Clock, Depends(get_clock)]
SettingsDep = Annotated[Settings, Depends(get_settings)]
PspDep = Annotated[PspClient, Depends(get_psp_client)]
IdempotencyKey = Annotated[uuid.UUID, Header(alias="Idempotency-Key")]
Feature = Literal[
    "bet_creation_enabled", "stakes_enabled", "deposits_enabled", "withdrawals_enabled"
]


def protected_router(**kwargs: Any) -> APIRouter:
    dependencies = [Depends(current_user), *kwargs.pop("dependencies", [])]
    return APIRouter(dependencies=dependencies, **kwargs)


def require_feature(name: Feature) -> Callable[[Settings], None]:
    def check(settings: SettingsDep) -> None:
        if not getattr(settings, name):
            raise ApiError(503, ErrorCode.FEATURE_DISABLED)

    return check


def get_receipt_signer(request: Request) -> ReceiptSigner:
    signer: ReceiptSigner = request.app.state.receipt_signer
    return signer


def trace_for(request: Request, user: CurrentUser, idempotency_key: str | None = None) -> Trace:
    return Trace(
        actor_id=user.id,
        session_id=user.session_id,
        request_id=request_id_of(request),
        idempotency_key=idempotency_key,
    )
