import re
import time
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from mock_psp import webhooks
from mock_psp.main import create_mock_app
from mock_psp.settings import MockPspSettings
from toutbet.security.webhook_signature import verify_webhook

API_KEY = "k" * 40
WEBHOOK_SECRET = "w" * 40


@pytest.fixture
def sent(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []

    def fake_post(url: str, **kwargs: Any) -> httpx.Response:
        calls.append({"url": url, **kwargs})
        return httpx.Response(200)

    monkeypatch.setattr(webhooks.httpx, "post", fake_post)
    return calls


@pytest.fixture
def mock(sent: list[dict[str, Any]]) -> TestClient:
    settings = MockPspSettings(
        _env_file=None,
        PSP_API_KEY=API_KEY,
        PSP_WEBHOOK_SECRET=WEBHOOK_SECRET,
        MOCK_PSP_PUBLIC_URL="http://psp.local",
        MOCK_PSP_WEBHOOK_URL="http://app.local/api/webhooks/psp",
    )
    return TestClient(create_mock_app(settings))


def _checkout(mock: TestClient) -> str:
    response = mock.post(
        "/v1/checkout_sessions",
        json={
            "reference": "dep1",
            "amount_cents": 2000,
            "currency": "EUR",
            "return_url": "https://toutbet.test/#/wallet",
        },
        headers={"Authorization": f"Bearer {API_KEY}"},
    )
    assert response.status_code == 200
    session_id: str = response.json()["session_id"]
    return session_id


def test_mock_api_requires_the_shared_key(mock: TestClient) -> None:
    body = {"reference": "u1", "return_url": "https://toutbet.test/"}

    assert mock.post("/v1/kyc_sessions", json=body).status_code == 401
    wrong = {"Authorization": "Bearer nope"}
    assert mock.post("/v1/kyc_sessions", json=body, headers=wrong).status_code == 401


def test_payment_page_has_no_card_field_and_strict_csp(mock: TestClient) -> None:
    session_id = _checkout(mock)

    response = mock.get(f"/pay/{session_id}")

    assert response.status_code == 200
    assert "<input" not in response.text
    assert ">Payer<" in response.text
    assert ">Refuser<" in response.text
    assert not re.search(r"<script|style=|onclick", response.text)
    assert "unsafe-inline" not in response.headers["content-security-policy"]


def test_confirmed_payment_sends_signed_webhook(
    mock: TestClient, sent: list[dict[str, Any]]
) -> None:
    session_id = _checkout(mock)

    response = mock.post(f"/pay/{session_id}/confirm")

    assert response.status_code == 200
    assert 'href="https://toutbet.test/#/wallet"' in response.text
    call = sent[0]
    assert call["url"] == "http://app.local/api/webhooks/psp"
    header = call["headers"]["X-PSP-Signature"]
    assert verify_webhook(WEBHOOK_SECRET.encode(), header, call["content"], int(time.time()))
    assert b'"payment.succeeded"' in call["content"]
    assert mock.post(f"/pay/{session_id}/confirm").status_code == 404


def test_kyc_page_offers_validation_button(mock: TestClient, sent: list[dict[str, Any]]) -> None:
    session = mock.post(
        "/v1/kyc_sessions",
        json={"reference": "user-1", "return_url": "https://toutbet.test/#/wallet"},
        headers={"Authorization": f"Bearer {API_KEY}"},
    ).json()["session_id"]

    page = mock.get(f"/kyc/{session}")
    mock.post(f"/kyc/{session}/verify")

    assert "Valider mon identité" in page.text
    assert b'"kyc.verified"' in sent[0]["content"]


def _payout(mock: TestClient, key: str | None, reference: str = "wd-1") -> httpx.Response:
    headers = {"Authorization": f"Bearer {API_KEY}"}
    if key is not None:
        headers["Idempotency-Key"] = key
    body = {"reference": reference, "user_reference": "u1", "amount_cents": 2000, "currency": "EUR"}
    return mock.post("/v1/payouts", json=body, headers=headers)


def test_payout_is_created_once_per_idempotency_key(mock: TestClient) -> None:
    auth = {"Authorization": f"Bearer {API_KEY}"}

    first = _payout(mock, "wd-1")
    replay = _payout(mock, "wd-1")
    missing_key = _payout(mock, None, reference="wd-2")
    found = mock.get("/v1/payouts/wd-1", headers=auth)
    unknown = mock.get("/v1/payouts/wd-2", headers=auth)

    assert first.status_code == replay.status_code == 200
    assert first.json()["payout_id"] == replay.json()["payout_id"]
    assert missing_key.status_code == 422
    assert found.json() == {"payout_id": first.json()["payout_id"], "status": "paid"}
    assert unknown.status_code == 404
    assert mock.get("/v1/payouts/wd-1").status_code == 401


def test_toutbet_never_imports_mock_psp() -> None:
    source = Path(__file__).parents[2] / "src" / "toutbet"

    for path in source.rglob("*.py"):
        assert "mock_psp" not in path.read_text(encoding="utf-8"), path.name
