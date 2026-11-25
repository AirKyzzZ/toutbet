import inspect
import json
from collections.abc import Callable

import httpx
import pytest

from support import random_seed
from toutbet.config import Settings
from toutbet.main import create_app
from toutbet.psp.client import (
    HttpPspClient,
    PayoutStatus,
    PspClient,
    PspRefusedError,
    PspUnavailableError,
)


def test_timeouts_are_configured() -> None:
    client = HttpPspClient("https://psp.test", "k" * 40)

    assert client.timeout.connect == 2.0
    assert client.timeout.read == 5.0
    assert client.timeout.write is not None
    assert client.timeout.pool is not None


def test_base_url_comes_from_settings_only() -> None:
    settings = Settings(
        _env_file=None,
        database_url="sqlite://",
        jwt_secret="j" * 40,
        receipt_signing_key=random_seed(),
        psp_base_url="https://sandbox.psp.example",
        psp_api_key="a" * 40,
        psp_webhook_secret="w" * 40,
        public_base_url="https://toutbet.test",
    )
    app = create_app(settings)
    seen: list[httpx.Request] = []

    def capture(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"session_id": "cs_1", "redirect_url": "https://p/x"})

    client = HttpPspClient(settings.psp_base_url, "a" * 40, transport=httpx.MockTransport(capture))
    client.create_checkout(
        reference="r1", amount_cents=500, currency="EUR", return_url="http://evil.test/"
    )
    client.create_kyc_session(reference="u1", return_url="http://169.254.169.254/")

    assert app.state.psp_client.base_url.rstrip("/") == "https://sandbox.psp.example"
    assert {request.url.host for request in seen} == {"sandbox.psp.example"}
    assert all(request.headers["authorization"] == "Bearer " + "a" * 40 for request in seen)
    for name in ("create_checkout", "create_kyc_session", "create_payout", "find_payout"):
        parameters = inspect.signature(getattr(PspClient, name)).parameters
        assert not {"url", "base_url", "host", "endpoint"} & set(parameters)


@pytest.mark.parametrize(
    ("handler", "error"),
    [
        (lambda request: (_ for _ in ()).throw(httpx.ReadTimeout("slow")), PspUnavailableError),
        (lambda request: (_ for _ in ()).throw(httpx.ConnectError("down")), PspUnavailableError),
        (lambda request: httpx.Response(503), PspUnavailableError),
        (lambda request: httpx.Response(402, json={}), PspRefusedError),
        (
            lambda request: httpx.Response(
                200, json={"session_id": "cs_1", "redirect_url": "javascript:alert(1)"}
            ),
            PspUnavailableError,
        ),
    ],
)
def test_psp_failures_are_typed_and_never_retried(
    handler: Callable[[httpx.Request], httpx.Response], error: type[Exception]
) -> None:
    calls: list[str] = []

    def transport(request: httpx.Request) -> httpx.Response:
        calls.append(json.loads(request.content)["reference"])
        return handler(request)

    client = HttpPspClient("https://psp.test", "k" * 40, transport=httpx.MockTransport(transport))

    with pytest.raises(error):
        client.create_checkout(reference="r1", amount_cents=500, currency="EUR", return_url="x")

    assert calls == ["r1"]


def test_payout_sends_withdrawal_reference_as_idempotency_key() -> None:
    seen: list[httpx.Request] = []

    def capture(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"payout_id": "po_1"})

    client = HttpPspClient("https://psp.test", "k" * 40, transport=httpx.MockTransport(capture))

    client.create_payout(reference="wd-1", user_reference="u1", amount_cents=2000, currency="EUR")

    assert seen[0].headers["idempotency-key"] == "wd-1"
    assert json.loads(seen[0].content)["reference"] == "wd-1"


@pytest.mark.parametrize(
    ("handler", "expected"),
    [
        (
            lambda request: httpx.Response(200, json={"payout_id": "po_1", "status": "paid"}),
            PayoutStatus("po_1", "paid"),
        ),
        (
            lambda request: httpx.Response(200, json={"payout_id": "po_1", "status": "refused"}),
            PayoutStatus("po_1", "refused"),
        ),
        (lambda request: httpx.Response(404, json={}), None),
    ],
)
def test_find_payout_reports_what_the_psp_knows(
    handler: Callable[[httpx.Request], httpx.Response], expected: PayoutStatus | None
) -> None:
    paths: list[str] = []

    def transport(request: httpx.Request) -> httpx.Response:
        paths.append(f"{request.method} {request.url.path}")
        return handler(request)

    client = HttpPspClient("https://psp.test", "k" * 40, transport=httpx.MockTransport(transport))

    assert client.find_payout(reference="wd-1") == expected
    assert paths == ["GET /v1/payouts/wd-1"]


@pytest.mark.parametrize(
    "handler",
    [
        lambda request: (_ for _ in ()).throw(httpx.ReadTimeout("slow")),
        lambda request: httpx.Response(503),
        lambda request: httpx.Response(401, json={}),
        lambda request: httpx.Response(200, json={"payout_id": "po_1", "status": "maybe"}),
    ],
)
def test_find_payout_failures_are_unknown_outcomes(
    handler: Callable[[httpx.Request], httpx.Response],
) -> None:
    client = HttpPspClient("https://psp.test", "k" * 40, transport=httpx.MockTransport(handler))

    with pytest.raises(PspUnavailableError):
        client.find_payout(reference="wd-1")
