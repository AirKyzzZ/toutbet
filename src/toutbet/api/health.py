from fastapi import APIRouter

from toutbet.rate_limit import RateLimits
from toutbet.schemas.common import StatusResponse


def build_router(limits: RateLimits) -> APIRouter:
    router = APIRouter(tags=["health"])

    @router.get("/healthz", response_model=StatusResponse)
    def healthz() -> StatusResponse:
        return StatusResponse(status="ok")

    return router
