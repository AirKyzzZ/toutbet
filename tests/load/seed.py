import argparse
import json
import secrets
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx

MAX_ACTIVE_INVITATIONS = 50


class Seeder:
    def __init__(self, host: str, psp: str, workers: int) -> None:
        limits = httpx.Limits(max_connections=workers, max_keepalive_connections=workers)
        self.client = httpx.Client(base_url=host, timeout=60, limits=limits)
        self.psp = psp
        self.run = secrets.token_hex(3)
        self.password = secrets.token_urlsafe(18)

    def _check(self, response: httpx.Response, status: int) -> dict[str, Any]:
        if response.status_code != status:
            raise RuntimeError(
                f"{response.request.url.path}: {response.status_code} {response.text}"
            )
        body: dict[str, Any] = response.json()
        return body

    def account(self, name: str) -> tuple[str, str]:
        email = f"{name}-{self.run}@example.org"
        self._check(
            self.client.post(
                "/api/auth/register",
                json={
                    "email": email,
                    "display_name": f"{name}_{self.run}",
                    "password": self.password,
                    "birth_date": "1990-05-17",
                },
            ),
            202,
        )
        login = self.client.post(
            "/api/auth/login", json={"email": email, "password": self.password}
        )
        return email, self._check(login, 200)["access_token"]

    def headers(self, token: str) -> dict[str, str]:
        return {"Authorization": f"Bearer {token}"}

    def fund(self, token: str, cents: int) -> None:
        deposit = self._check(
            self.client.post(
                "/api/deposits",
                json={"amount_cents": cents},
                headers={**self.headers(token), "Idempotency-Key": str(uuid.uuid4())},
            ),
            201,
        )
        session_id = deposit["redirect_url"].rsplit("/", 1)[1]
        paid = httpx.post(f"{self.psp}/pay/{session_id}/confirm", timeout=60)
        if "a été notifié" not in paid.text:
            raise RuntimeError(f"mock PSP did not notify ToutBet' for {session_id}")

    def create_bet(self, token: str) -> str:
        now = datetime.now(UTC)
        bet = self._check(
            self.client.post(
                "/api/bets",
                json={
                    "title": "Finale : Matis marque-t-il avant la mi-temps ?",
                    "closes_at": (now + timedelta(hours=6)).isoformat(),
                    "event_starts_at": (now + timedelta(hours=7)).isoformat(),
                    "bookie_commission_bps": 300,
                },
                headers=self.headers(token),
            ),
            201,
        )
        bet_id: str = bet["id"]
        return bet_id

    def invite(self, bookie: str, bet_id: str) -> str:
        created = self._check(
            self.client.post(f"/api/bets/{bet_id}/invitations", headers=self.headers(bookie)),
            201,
        )
        url: str = created["invitation_url"]
        return url.rsplit("/", 1)[1]

    def join(self, token: str, invitation: str) -> None:
        self._check(
            self.client.post(
                "/api/invitations/accept",
                json={"token": invitation},
                headers=self.headers(token),
            ),
            200,
        )


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed one bookie, one bet and funded bettors.")
    parser.add_argument("--host", default="http://127.0.0.1:8200")
    parser.add_argument("--psp", default="http://127.0.0.1:8201")
    parser.add_argument("--bettors", type=int, default=500)
    parser.add_argument("--funds-cents", type=int, default=2000)
    parser.add_argument("--workers", type=int, default=16)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()

    seeder = Seeder(args.host, args.psp, args.workers)
    _, bookie = seeder.account("bookie")
    bet_id = seeder.create_bet(bookie)

    def bettor(index: int) -> tuple[str, str]:
        email, token = seeder.account(f"load{index}")
        seeder.fund(token, args.funds_cents)
        return email, token

    with ThreadPoolExecutor(args.workers) as pool:
        bettors = list(pool.map(bettor, range(args.bettors)))
        for start in range(0, len(bettors), MAX_ACTIVE_INVITATIONS):
            batch = bettors[start : start + MAX_ACTIVE_INVITATIONS]
            invitations = [seeder.invite(bookie, bet_id) for _ in batch]
            list(pool.map(seeder.join, [token for _, token in batch], invitations))

    args.out.write_text(
        json.dumps(
            {
                "bet_id": bet_id,
                "password": seeder.password,
                "bettors": [email for email, _ in bettors],
                "tokens": [token for _, token in bettors],
            }
        ),
        encoding="utf-8",
    )
    print(f"bet {bet_id}, {len(bettors)} bettors funded and joined, seed in {args.out}")


if __name__ == "__main__":
    main()
