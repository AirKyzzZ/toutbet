from collections.abc import Callable

from fastapi.testclient import TestClient

from toutbet.middleware.security_headers import SECURITY_HEADERS

EXPECTED_CSP = (
    "default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
    "connect-src 'self'; font-src 'self'; manifest-src 'self'; base-uri 'none'; "
    "form-action 'self'; frame-ancestors 'none'"
)


def test_security_headers_on_api_and_static(client: TestClient) -> None:
    responses = {
        "api_error": client.get("/api/me"),
        "api_public": client.get("/api/receipts/public-key"),
        "index": client.get("/"),
        "script": client.get("/js/main.js"),
        "stylesheet": client.get("/app.css"),
        "missing": client.get("/does-not-exist"),
        "health": client.get("/healthz"),
    }

    assert SECURITY_HEADERS["Content-Security-Policy"] == EXPECTED_CSP
    for name, response in responses.items():
        for header, value in SECURITY_HEADERS.items():
            assert response.headers.get(header) == value, (name, header)
        assert "server" not in response.headers, name
        assert "x-request-id" in response.headers, name
    assert responses["api_error"].headers["cache-control"] == "no-store"
    assert responses["api_public"].headers["cache-control"] == "no-store"
    assert responses["script"].headers["content-type"].startswith("text/javascript")
    assert (
        responses["index"].headers["strict-transport-security"]
        == "max-age=63072000; includeSubDomains"
    )


def test_cors_rejects_unknown_origin(make_client: Callable[..., TestClient]) -> None:
    client = make_client(cors_origins=["https://app.toutbet.test"])
    preflight = {"Access-Control-Request-Method": "POST"}

    evil = client.options("/api/bets", headers={"Origin": "https://evil.test", **preflight})
    simple = client.get("/healthz", headers={"Origin": "https://evil.test"})
    allowed = client.options(
        "/api/bets", headers={"Origin": "https://app.toutbet.test", **preflight}
    )

    assert evil.status_code == 400
    assert "access-control-allow-origin" not in evil.headers
    assert "access-control-allow-origin" not in simple.headers
    assert allowed.headers["access-control-allow-origin"] == "https://app.toutbet.test"
    assert "access-control-allow-credentials" not in allowed.headers
    assert "PUT" not in allowed.headers["access-control-allow-methods"]


def test_cors_is_closed_by_default(client: TestClient) -> None:
    response = client.options(
        "/api/bets",
        headers={"Origin": "https://toutbet.test", "Access-Control-Request-Method": "POST"},
    )

    assert response.status_code == 400
    assert "access-control-allow-origin" not in response.headers


def test_unknown_host_rejected(client: TestClient) -> None:
    response = client.post(
        "/api/auth/register",
        json={"email": "a@b.org", "display_name": "x" * 5, "password": "y" * 12},
        headers={"Host": "evil.example"},
    )

    assert response.status_code == 400
    assert "content-security-policy" in response.headers
    assert client.get("/healthz", headers={"Host": "evil.example"}).status_code == 400
