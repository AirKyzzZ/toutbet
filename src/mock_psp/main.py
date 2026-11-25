import asyncio
import time
from collections.abc import Awaitable, Callable

import uvicorn
from fastapi import FastAPI, Request, Response

from mock_psp.models import Store
from mock_psp.routes import register_api, register_pages
from mock_psp.settings import MockPspSettings
from toutbet.middleware.security_headers import SecurityHeadersMiddleware
from toutbet.observability import configure_logging, log_event


def slow_down_api(app: FastAPI, settings: MockPspSettings, clock: Callable[[], float]) -> None:
    started = clock()
    delay = settings.latency_ms / 1000

    def still_slow() -> bool:
        return not settings.latency_duration_s or clock() - started < settings.latency_duration_s

    @app.middleware("http")
    async def delay_api_answers(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        # The handler runs first, so a payout already exists when the caller gives up waiting.
        response = await call_next(request)
        if request.url.path.startswith("/v1/") and still_slow():
            await asyncio.sleep(delay)
        return response

    log_event(
        "mock_psp.latency_enabled",
        latency_ms=settings.latency_ms,
        duration_s=settings.latency_duration_s,
    )


def create_mock_app(
    settings: MockPspSettings, clock: Callable[[], float] = time.monotonic
) -> FastAPI:
    app = FastAPI(title="Mock PSP", docs_url=None, redoc_url=None, openapi_url=None)
    app.add_middleware(SecurityHeadersMiddleware)
    if settings.latency_ms:
        slow_down_api(app, settings, clock)
    store = Store()
    register_api(app, settings, store)
    register_pages(app, settings, store)
    return app


def run() -> None:
    configure_logging()
    settings = MockPspSettings()
    uvicorn.run(
        create_mock_app(settings),
        host=settings.host,
        port=settings.port,
        server_header=False,
        access_log=False,
        log_config=None,
    )
