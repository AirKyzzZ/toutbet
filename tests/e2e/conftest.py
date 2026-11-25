import os
import re
import secrets
import socket
import subprocess
import sys
import time
from collections.abc import Callable, Iterator
from contextlib import ExitStack, contextmanager
from pathlib import Path
from typing import Any

import httpx
import pytest
from browser_flows import PASSWORD, Api, Phone, Stack
from playwright.sync_api import Browser, BrowserContext, ConsoleMessage, Dialog, Request, expect

from toutbet.db import make_engine
from toutbet.models import create_schema
from toutbet.security.receipts import b64url

HOST = "127.0.0.1"
APP_PORT = 8200
PSP_PORT = 8201
STRICT_APP_PORT = 8202
NO_TRUSTED_PROXY = "192.0.2.1"
GENEROUS_LIMIT = "100000/minute"
RATE_LIMIT_VARIABLES = (
    "RATE_LIMIT_LOGIN",
    "RATE_LIMIT_REGISTER",
    "RATE_LIMIT_REFRESH",
    "RATE_LIMIT_WRITE",
    "RATE_LIMIT_READ",
    "RATE_LIMIT_WRITE_PER_IP",
    "RATE_LIMIT_READ_PER_IP",
    "RATE_LIMIT_KYC",
    "RATE_LIMIT_WITHDRAWALS",
    "RATE_LIMIT_INVITATION_ACCEPT",
    "RATE_LIMIT_WEBHOOK",
)
MOBILE = {
    "viewport": {"width": 375, "height": 812},
    "device_scale_factor": 3,
    "is_mobile": True,
    "has_touch": True,
    "locale": "fr-FR",
    "timezone_id": "Europe/Paris",
}
CSP_LISTENER = """
document.addEventListener("securitypolicyviolation", (event) => {
  window.__e2eReportCsp(`${event.violatedDirective} ${event.blockedURI} ${document.URL}`);
});
"""
JWT_IN_LOGS = re.compile(r"eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.")

expect.set_options(timeout=10_000)


def _generated_secrets() -> dict[str, str]:
    return {
        "JWT_SECRET": secrets.token_urlsafe(48),
        "RECEIPT_SIGNING_KEY": b64url(secrets.token_bytes(32)),
        "PSP_WEBHOOK_SECRET": secrets.token_urlsafe(48),
        "PSP_API_KEY": secrets.token_urlsafe(48),
    }


def _ensure_free(port: int) -> None:
    with socket.socket() as probe:
        if probe.connect_ex((HOST, port)) == 0:
            raise RuntimeError(f"port {port} is already in use, stop what listens there first")


def _wait_ready(url: str, process: subprocess.Popen[bytes], log: Path) -> None:
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"{url} exited early:\n{log.read_text(errors='replace')[-3000:]}")
        try:
            if httpx.get(url, timeout=1).status_code == 200:
                return
        except httpx.TransportError:
            pass
        time.sleep(0.2)
    raise RuntimeError(f"{url} not ready after 30 s, see {log}")


@contextmanager
def _running(
    command: list[str], env: dict[str, str], workdir: Path, port: int, ready_url: str, log: Path
) -> Iterator[None]:
    _ensure_free(port)
    with log.open("wb") as sink:
        process = subprocess.Popen(
            command, env=env, cwd=workdir, stdout=sink, stderr=subprocess.STDOUT
        )
        try:
            _wait_ready(ready_url, process, log)
            yield
        finally:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()


def _check_app_log(log: Path) -> None:
    text = log.read_text(errors="replace")
    problems = [line for line in text.splitlines() if "error.unhandled" in line]
    if PASSWORD in text:
        problems.append("a password reached the logs")
    if JWT_IN_LOGS.search(text):
        problems.append("a JWT reached the logs")
    if problems:
        pytest.fail(f"server log {log} shows problems:\n" + "\n".join(problems[:20]))


@contextmanager
def _launch(workdir: Path, port: int, *, with_psp: bool, generous_limits: bool) -> Iterator[Stack]:
    workdir.mkdir(parents=True)
    database_url = f"sqlite:///{workdir / 'toutbet.db'}"
    engine = make_engine(database_url)
    create_schema(engine)
    engine.dispose()
    shared = _generated_secrets()
    base_url = f"http://{HOST}:{port}"
    psp_url = f"http://{HOST}:{PSP_PORT}"
    env = {**os.environ, **shared, "PYTHONUNBUFFERED": "1"}
    app_env = {
        **env,
        "ENV": "test",
        "DATABASE_URL": database_url,
        "PSP_BASE_URL": psp_url,
        "PUBLIC_BASE_URL": base_url,
        "ALLOWED_HOSTS": f"{HOST},localhost",
        "CORS_ORIGINS": "",
        "TRUSTED_PROXY_IPS": NO_TRUSTED_PROXY,
    }
    for variable in RATE_LIMIT_VARIABLES:
        app_env.pop(variable, None)
        if generous_limits:
            app_env[variable] = GENEROUS_LIMIT
    psp_env = {
        **env,
        "MOCK_PSP_HOST": HOST,
        "MOCK_PSP_PORT": str(PSP_PORT),
        "MOCK_PSP_PUBLIC_URL": psp_url,
        "MOCK_PSP_WEBHOOK_URL": f"{base_url}/api/webhooks/psp",
    }
    app_command = [
        sys.executable,
        "-m",
        "uvicorn",
        "--factory",
        "toutbet.main:build_app",
        "--host",
        HOST,
        "--port",
        str(port),
        "--no-server-header",
        "--no-access-log",
        "--no-proxy-headers",
    ]
    psp_command = [sys.executable, "-c", "from mock_psp.main import run; run()"]
    app_log = workdir / "app.log"
    with ExitStack() as processes:
        if with_psp:
            processes.enter_context(
                _running(
                    psp_command,
                    psp_env,
                    workdir,
                    PSP_PORT,
                    f"{psp_url}/assets/mock.css",
                    workdir / "mock-psp.log",
                )
            )
        processes.enter_context(
            _running(app_command, app_env, workdir, port, f"{base_url}/healthz", app_log)
        )
        yield Stack(base_url, psp_url, database_url, shared["PSP_WEBHOOK_SECRET"])
    _check_app_log(app_log)


@pytest.fixture(scope="session")
def stack(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Stack]:
    workdir = tmp_path_factory.mktemp("e2e") / "stack"
    with _launch(workdir, APP_PORT, with_psp=True, generous_limits=True) as running:
        yield running


@pytest.fixture
def strict_stack(tmp_path: Path) -> Iterator[Stack]:
    with _launch(
        tmp_path / "strict", STRICT_APP_PORT, with_psp=False, generous_limits=False
    ) as running:
        yield running


def _track(context: BrowserContext, phone: Phone) -> None:
    def report_csp(_source: Any, violation: str) -> None:
        phone.csp_violations.append(violation)

    def on_console(message: ConsoleMessage) -> None:
        if "Content Security Policy" in message.text:
            phone.csp_violations.append(message.text)

    def on_dialog(dialog: Dialog) -> None:
        phone.dialogs.append(dialog.message)
        dialog.dismiss()

    def on_request(request: Request) -> None:
        phone.requested_urls.append(request.url)

    context.expose_binding("__e2eReportCsp", report_csp)
    context.add_init_script(CSP_LISTENER)
    phone.page.on("console", on_console)
    phone.page.on("dialog", on_dialog)
    phone.page.on("request", on_request)


@contextmanager
def _phone_factory(browser: Browser, default: Stack) -> Iterator[Callable[..., Phone]]:
    contexts: list[BrowserContext] = []
    phones: list[Phone] = []

    def open_phone(target: Stack = default) -> Phone:
        context = browser.new_context(**MOBILE)
        contexts.append(context)
        page = context.new_page()
        page.set_default_timeout(10_000)
        phone = Phone(page, target)
        _track(context, phone)
        phones.append(phone)
        return phone

    yield open_phone
    for context in contexts:
        context.close()
    for phone in phones:
        assert phone.csp_violations == [], phone.csp_violations
        assert phone.dialogs == [], phone.dialogs


@pytest.fixture
def phones(browser: Browser, stack: Stack) -> Iterator[Callable[..., Phone]]:
    with _phone_factory(browser, stack) as factory:
        yield factory


@pytest.fixture
def phone(phones: Callable[..., Phone]) -> Phone:
    return phones()


@pytest.fixture
def strict_phone(browser: Browser, strict_stack: Stack) -> Iterator[Phone]:
    with _phone_factory(browser, strict_stack) as factory:
        yield factory()


@pytest.fixture
def api(stack: Stack) -> Iterator[Api]:
    client = Api(stack)
    yield client
    client.close()
