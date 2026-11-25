import json
import logging

from fastapi import APIRouter, Request
from pydantic import ValidationError
from starlette.concurrency import run_in_threadpool

from toutbet.api.deps import ClockDep, SettingsDep
from toutbet.db import DbSession
from toutbet.errors import ApiError, ErrorCode, request_id_of
from toutbet.observability import log_event
from toutbet.rate_limit import RateLimits, client_ip
from toutbet.schemas.common import StatusResponse
from toutbet.security.webhook_signature import SIGNATURE_HEADER, verify_webhook
from toutbet.services.psp_events import process_webhook


def build_router(limits: RateLimits) -> APIRouter:
    router = APIRouter(prefix="/api/webhooks", tags=["webhooks"])

    @router.post("/psp", response_model=StatusResponse)
    @limits.per_ip(limits.settings.rate_limit_webhook)
    async def psp_webhook(
        request: Request, db: DbSession, clock: ClockDep, settings: SettingsDep
    ) -> StatusResponse:
        raw_body = await request.body()
        now = clock.now()
        secret = settings.psp_webhook_secret.get_secret_value().encode("utf-8")
        header = request.headers.get(SIGNATURE_HEADER)
        if not verify_webhook(secret, header, raw_body, int(now.timestamp())):
            log_event("psp.webhook_rejected", level=logging.WARNING, client_ip=client_ip(request))
            raise ApiError(400, ErrorCode.INVALID_SIGNATURE)
        try:
            payload = json.loads(raw_body)
            await run_in_threadpool(process_webhook, db, payload, now, request_id_of(request))
        except (ValueError, ValidationError):
            log_event("psp.webhook_malformed", level=logging.WARNING)
            raise ApiError(400, ErrorCode.BAD_REQUEST) from None
        return StatusResponse(status="ok")

    return router
