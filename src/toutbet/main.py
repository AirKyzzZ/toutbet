import mimetypes
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from slowapi.errors import RateLimitExceeded
from starlette.middleware.trustedhost import TrustedHostMiddleware
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

from toutbet.api import build_routers
from toutbet.clock import SystemClock
from toutbet.config import Settings, decode_seed, load_settings
from toutbet.db import make_engine, make_session_factory
from toutbet.errors import register_exception_handlers
from toutbet.middleware.body_limit import BodyLimitMiddleware
from toutbet.middleware.request_id import RequestContextMiddleware
from toutbet.middleware.security_headers import SecurityHeadersMiddleware
from toutbet.observability import configure_logging
from toutbet.psp.client import HttpPspClient
from toutbet.rate_limit import RateLimits, build_limiter, rate_limit_exceeded_handler
from toutbet.security.receipts import ReceiptSigner

STATIC_DIR = Path(__file__).parent / "static"
mimetypes.add_type("application/manifest+json", ".webmanifest")
mimetypes.add_type("text/javascript", ".js")


def create_app(settings: Settings) -> FastAPI:
    engine = make_engine(settings.database_url.get_secret_value())
    psp_client = HttpPspClient(settings.psp_base_url, settings.psp_api_key.get_secret_value())

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        yield
        psp_client.close()
        engine.dispose()

    dev = settings.env == "dev"
    app = FastAPI(
        title="ToutBet'",
        debug=False,
        docs_url="/docs" if dev else None,
        redoc_url="/redoc" if dev else None,
        openapi_url="/openapi.json" if dev else None,
        lifespan=lifespan,
    )
    limiter = build_limiter()
    app.state.settings = settings
    app.state.engine = engine
    app.state.session_factory = make_session_factory(engine)
    app.state.clock = SystemClock()
    app.state.psp_client = psp_client
    app.state.receipt_signer = ReceiptSigner(
        decode_seed(settings.receipt_signing_key.get_secret_value())
    )
    app.state.limiter = limiter

    register_exception_handlers(app)
    app.add_exception_handler(RateLimitExceeded, rate_limit_exceeded_handler)
    for router in build_routers(RateLimits(limiter, settings)):
        app.include_router(router)
    app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")

    app.add_middleware(BodyLimitMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=False,
        allow_methods=["GET", "POST"],
        allow_headers=["Authorization", "Content-Type", "Idempotency-Key"],
        max_age=600,
    )
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=settings.allowed_hosts)
    app.add_middleware(RequestContextMiddleware)
    app.add_middleware(SecurityHeadersMiddleware)
    app.add_middleware(ProxyHeadersMiddleware, trusted_hosts=settings.trusted_proxy_ips)
    return app


def build_app() -> FastAPI:
    configure_logging()
    return create_app(load_settings())
