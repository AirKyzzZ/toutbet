from collections.abc import Callable, Iterator

from fastapi.testclient import TestClient
from sqlalchemy import select

from support import Api, FakeClock, FakePsp
from toutbet.models import Bet


def test_as13_write_rate_limit_returns_429_with_retry_after(
    make_client: Callable[..., TestClient], clock: FakeClock, psp: FakePsp
) -> None:
    api = Api(make_client(rate_limit_write="20/minute"), clock, psp)
    bookie = api.signup()
    other = api.signup()

    statuses = [
        api.client.post("/api/bets", json=api.bet_body(), headers=bookie.headers) for _ in range(21)
    ]

    assert [r.status_code for r in statuses[:20]] == [201] * 20
    assert statuses[20].status_code == 429
    assert statuses[20].json()["error"]["code"] == "RATE_LIMITED"
    assert int(statuses[20].headers["retry-after"]) > 0
    with api.db() as db:
        assert len(db.scalars(select(Bet)).all()) == 20
    assert (
        api.client.post("/api/bets", json=api.bet_body(), headers=other.headers).status_code == 201
    )


def test_as13_oversized_body_returns_413(api: Api, client: TestClient) -> None:
    bookie = api.signup()
    body = api.bet_body(description="x" * (70 * 1024))

    response = client.post("/api/bets", json=body, headers=bookie.headers)

    assert response.status_code == 413
    assert response.json()["error"]["code"] == "PAYLOAD_TOO_LARGE"
    assert "content-security-policy" in response.headers
    with api.db() as db:
        assert db.scalars(select(Bet)).all() == []


def test_as13_streamed_body_without_length_is_cut_at_64_kib(api: Api, client: TestClient) -> None:
    bookie = api.signup()

    def chunks() -> Iterator[bytes]:
        yield b'{"title": "'
        for _ in range(80):
            yield b"x" * 1024
        yield b'"}'

    response = client.post(
        "/api/bets",
        content=chunks(),
        headers={**bookie.headers, "Content-Type": "application/json"},
    )

    assert response.status_code == 413


def test_as13_page_size_capped_at_50(api: Api, client: TestClient) -> None:
    bookie = api.signup()
    for _ in range(55):
        api.create_bet(bookie)

    default_page = client.get("/api/bets", headers=bookie.headers).json()
    oversized = client.get("/api/bets?page_size=1000", headers=bookie.headers)
    second_page = client.get("/api/bets?page=2", headers=bookie.headers).json()

    assert len(default_page["items"]) == 50
    assert oversized.status_code == 422
    assert len(second_page["items"]) == 5
    assert client.get("/api/wallet?page_size=500", headers=bookie.headers).status_code == 422
