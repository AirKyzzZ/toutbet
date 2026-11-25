import asyncio
import os
import secrets
import subprocess
import sys
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest

BASE_URL = os.environ.get("TOUTBET_LIVE_URL", "")
PSP_URL = os.environ.get("TOUTBET_LIVE_PSP_URL", "http://127.0.0.1:8201")
DATABASE_URL = os.environ.get("TOUTBET_LIVE_DATABASE_URL", "")
BETS = 50
BALANCE_CENTS = 5000
STAKE_CENTS = 700
PASSWORD = secrets.token_urlsafe(18)

pytestmark = pytest.mark.skipif(
    not BASE_URL or not DATABASE_URL,
    reason="needs TOUTBET_LIVE_URL and TOUTBET_LIVE_DATABASE_URL of a running stack",
)


def _ok(response: httpx.Response, status: int) -> dict[str, Any]:
    assert response.status_code == status, f"{response.request.url.path}: {response.text}"
    body: dict[str, Any] = response.json() if response.content else {}
    return body


async def _account(client: httpx.AsyncClient, name: str) -> dict[str, str]:
    suffix = secrets.token_hex(4)
    email = f"{name}-{suffix}@example.org"
    _ok(
        await client.post(
            "/api/auth/register",
            json={
                "email": email,
                "display_name": f"{name}_{suffix}",
                "password": PASSWORD,
                "birth_date": "1990-05-17",
            },
        ),
        202,
    )
    login = _ok(
        await client.post("/api/auth/login", json={"email": email, "password": PASSWORD}), 200
    )
    return {"Authorization": f"Bearer {login['access_token']}"}


async def _fund(client: httpx.AsyncClient, auth: dict[str, str], cents: int) -> None:
    deposit = _ok(
        await client.post(
            "/api/deposits",
            json={"amount_cents": cents},
            headers={**auth, "Idempotency-Key": str(uuid.uuid4())},
        ),
        201,
    )
    session_id = deposit["redirect_url"].rsplit("/", 1)[1]
    async with httpx.AsyncClient(timeout=30) as psp:
        paid = await psp.post(f"{PSP_URL}/pay/{session_id}/confirm")
    assert "a été notifié" in paid.text


async def _open_bet(client: httpx.AsyncClient, bookie: dict[str, str], index: int) -> str:
    now = datetime.now(UTC)
    bet = _ok(
        await client.post(
            "/api/bets",
            json={
                "title": f"Pari simultané numéro {index}",
                "closes_at": (now + timedelta(hours=1)).isoformat(),
                "event_starts_at": (now + timedelta(hours=2)).isoformat(),
                "bookie_commission_bps": 0,
            },
            headers=bookie,
        ),
        201,
    )
    bet_id: str = bet["id"]
    return bet_id


async def _join(
    client: httpx.AsyncClient, bookie: dict[str, str], player: dict[str, str], bet_id: str
) -> None:
    invitation = _ok(await client.post(f"/api/bets/{bet_id}/invitations", headers=bookie), 201)
    token = invitation["invitation_url"].rsplit("/", 1)[1]
    _ok(await client.post("/api/invitations/accept", json={"token": token}, headers=player), 200)


async def _race() -> tuple[list[tuple[int, str]], int, int]:
    limits = httpx.Limits(max_connections=BETS + 10, max_keepalive_connections=BETS + 10)
    async with httpx.AsyncClient(base_url=BASE_URL, timeout=60, limits=limits) as client:
        bookie, player = await _account(client, "bookie"), await _account(client, "player")
        await _fund(client, player, BALANCE_CENTS)
        bet_ids = [await _open_bet(client, bookie, index) for index in range(BETS)]
        for bet_id in bet_ids:
            await _join(client, bookie, player, bet_id)
        start = asyncio.Event()

        async def stake(bet_id: str) -> tuple[int, str]:
            headers = {**player, "Idempotency-Key": str(uuid.uuid4())}
            await start.wait()
            response = await client.post(
                f"/api/bets/{bet_id}/stakes",
                json={"outcome": "OUI", "amount_cents": STAKE_CENTS},
                headers=headers,
            )
            code = response.json().get("error", {}).get("code", "") if response.content else ""
            return response.status_code, code

        tasks = [asyncio.create_task(stake(bet_id)) for bet_id in bet_ids]
        await asyncio.sleep(0.2)
        start.set()
        results = await asyncio.gather(*tasks)
        balance = _ok(await client.get("/api/wallet", headers=player), 200)["balance_cents"]
        mine = _ok(
            await client.get("/api/bets", params={"role": "participant"}, headers=player), 200
        )
        recorded = sum(1 for bet in mine["items"] if bet["my_stake"] is not None)
    return results, balance, recorded


def test_live_same_user_concurrent_stakes_never_overspend() -> None:
    results, balance, recorded = asyncio.run(_race())
    accepted = sum(1 for status, _ in results if status == 201)
    refusals = sorted({f"{status} {code}" for status, code in results if status != 201})
    verify = subprocess.run(
        [str(Path(sys.executable).parent / "toutbet-admin"), "verify-ledger"],
        env={**os.environ, "DATABASE_URL": DATABASE_URL},
        capture_output=True,
        text=True,
        check=False,
    )
    print(
        f"\n{BETS} mises simultanées de {STAKE_CENTS} c avec {BALANCE_CENTS} c : "
        f"{accepted} acceptées, {recorded} enregistrées, refus {refusals}, "
        f"solde final {balance} c, verify-ledger: {(verify.stdout + verify.stderr).strip()}"
    )

    expected = BALANCE_CENTS // STAKE_CENTS
    assert all(status < 500 for status, _ in results), refusals
    assert accepted == expected
    assert recorded == expected
    assert refusals == ["409 INSUFFICIENT_FUNDS"]
    assert balance == BALANCE_CENTS - expected * STAKE_CENTS
    assert balance >= 0
    assert verify.returncode == 0, verify.stderr
