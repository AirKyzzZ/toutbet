import uvicorn
from fastapi import FastAPI

from mock_psp.models import Store
from mock_psp.routes import register_api, register_pages
from mock_psp.settings import MockPspSettings
from toutbet.middleware.security_headers import SecurityHeadersMiddleware
from toutbet.observability import configure_logging


def create_mock_app(settings: MockPspSettings) -> FastAPI:
    app = FastAPI(title="Mock PSP", docs_url=None, redoc_url=None, openapi_url=None)
    app.add_middleware(SecurityHeadersMiddleware)
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
