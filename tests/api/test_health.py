from fastapi.testclient import TestClient


def test_healthz_returns_ok_without_details(client: TestClient) -> None:
    response = client.get("/healthz")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert "server" not in response.headers
    assert "x-request-id" in response.headers
