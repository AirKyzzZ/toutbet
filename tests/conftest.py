import io
import logging
import os
import secrets
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from support import Api, FakeClock, FakePsp, random_seed
from toutbet.clock import SystemClock, get_clock
from toutbet.config import Settings
from toutbet.db import make_engine, make_session_factory
from toutbet.main import create_app
from toutbet.models import Base, create_schema
from toutbet.observability import build_log_handler
from toutbet.psp.client import get_psp_client

GENEROUS_LIMIT = "100000/minute"
LIMIT_FIELDS = (
    "rate_limit_login",
    "rate_limit_register",
    "rate_limit_refresh",
    "rate_limit_write",
    "rate_limit_read",
    "rate_limit_kyc",
    "rate_limit_withdrawals",
    "rate_limit_invitation_accept",
    "rate_limit_webhook",
)
POSTGRES_URL = os.environ.get("DATABASE_URL", "")
USES_POSTGRES = POSTGRES_URL.startswith("postgresql")


def make_settings(database_url: str, **overrides: Any) -> Settings:
    values: dict[str, Any] = {
        "_env_file": None,
        "env": "test",
        "database_url": database_url,
        "jwt_secret": secrets.token_urlsafe(48),
        "receipt_signing_key": random_seed(),
        "psp_base_url": "https://psp.test",
        "psp_api_key": secrets.token_urlsafe(48),
        "psp_webhook_secret": secrets.token_urlsafe(48),
        "public_base_url": "https://toutbet.test",
        "allowed_hosts": ["testserver"],
        **dict.fromkeys(LIMIT_FIELDS, GENEROUS_LIMIT),
    }
    values.update(overrides)
    return Settings(**values)


def _admin_url() -> str:
    return os.environ.get("DATABASE_ADMIN_URL", POSTGRES_URL)


@pytest.fixture(scope="session")
def postgres_schema() -> None:
    engine = make_engine(_admin_url())
    Base.metadata.drop_all(engine)
    create_schema(engine)
    if "DATABASE_ADMIN_URL" in os.environ:
        grants = Path(__file__).parents[1] / "deploy" / "postgres" / "grants.sql"
        with engine.begin() as connection:
            connection.exec_driver_sql(grants.read_text(encoding="utf-8"))
    engine.dispose()


@pytest.fixture
def database_url(tmp_path: Path, request: pytest.FixtureRequest) -> str:
    if not USES_POSTGRES:
        url = f"sqlite:///{tmp_path / 'toutbet.db'}"
        engine = make_engine(url)
        create_schema(engine)
        engine.dispose()
        return url
    request.getfixturevalue("postgres_schema")
    engine = make_engine(_admin_url())
    with engine.begin() as connection:
        for table in reversed(Base.metadata.sorted_tables):
            connection.execute(table.delete())
    create_schema(engine)
    engine.dispose()
    return POSTGRES_URL


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock(SystemClock().now().replace(microsecond=0))


@pytest.fixture
def psp() -> FakePsp:
    return FakePsp()


@pytest.fixture
def make_client(
    database_url: str, clock: FakeClock, psp: FakePsp
) -> Iterator[Callable[..., TestClient]]:
    clients: list[TestClient] = []

    def factory(client_address: tuple[str, int] | None = None, **overrides: Any) -> TestClient:
        app = create_app(make_settings(database_url, **overrides))
        app.dependency_overrides[get_clock] = lambda: clock
        app.dependency_overrides[get_psp_client] = lambda: psp
        app.state.limiter.reset()
        extra = {} if client_address is None else {"client": client_address}
        test_client = TestClient(app, **extra)
        test_client.__enter__()
        clients.append(test_client)
        return test_client

    yield factory
    for test_client in clients:
        test_client.__exit__(None, None, None)


@pytest.fixture
def client(make_client: Callable[..., TestClient]) -> TestClient:
    return make_client()


@pytest.fixture
def api(client: TestClient, clock: FakeClock, psp: FakePsp) -> Api:
    return Api(client, clock, psp)


@pytest.fixture
def captured_logs() -> Iterator[io.StringIO]:
    stream = io.StringIO()
    handler = build_log_handler(stream)
    root = logging.getLogger()
    app_logger = logging.getLogger("toutbet")
    previous_level = app_logger.level
    root.addHandler(handler)
    app_logger.setLevel(logging.INFO)
    yield stream
    root.removeHandler(handler)
    app_logger.setLevel(previous_level)


@pytest.fixture
def db_session(database_url: str) -> Iterator[Session]:
    engine = make_engine(database_url)
    factory = make_session_factory(engine)
    with factory() as session:
        yield session
    engine.dispose()


@pytest.fixture
def owner_session(database_url: str) -> Iterator[Session]:
    engine = make_engine(_admin_url() if USES_POSTGRES else database_url)
    with make_session_factory(engine)() as session:
        yield session
    engine.dispose()
