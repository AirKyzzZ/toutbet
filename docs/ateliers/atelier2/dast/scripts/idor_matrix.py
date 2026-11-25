import base64
import json
import secrets
import sys
import uuid
from datetime import UTC, datetime, timedelta

import httpx

BASE_URL = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8100"
PASSWORD = secrets.token_urlsafe(18)


def signup(client: httpx.Client, name: str) -> dict[str, str]:
    email = f"{name}-{secrets.token_hex(4)}@dast.test"
    client.post(
        "/api/auth/register",
        json={
            "email": email,
            "display_name": f"{name}_{secrets.token_hex(3)}",
            "password": PASSWORD,
            "birth_date": "1990-01-01",
        },
    )
    token = client.post("/api/auth/login", json={"email": email, "password": PASSWORD}).json()
    return {"Authorization": f"Bearer {token['access_token']}"}


def forge_subject(headers: dict[str, str], subject: str) -> dict[str, str]:
    header, payload, signature = headers["Authorization"].removeprefix("Bearer ").split(".")
    claims = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
    claims["sub"] = subject
    forged = base64.urlsafe_b64encode(json.dumps(claims).encode()).rstrip(b"=").decode()
    return {"Authorization": f"Bearer {header}.{forged}.{signature}"}


def main() -> int:
    results: list[tuple[str, str, int, int]] = []
    with httpx.Client(base_url=BASE_URL, timeout=10) as client:

        def check(label: str, expected: int, response: httpx.Response) -> None:
            results.append((label, response.request.method, expected, response.status_code))

        alice = signup(client, "alice")
        bob = signup(client, "bob")
        carol = signup(client, "carol")
        now = datetime.now(UTC)
        bet = client.post(
            "/api/bets",
            json={
                "title": "Pari IDOR",
                "event_starts_at": (now + timedelta(days=2)).isoformat(),
                "closes_at": (now + timedelta(days=1)).isoformat(),
                "bookie_commission_bps": 100,
            },
            headers=alice,
        ).json()
        bet_url = f"/api/bets/{bet['id']}"
        stake = {"outcome": "OUI", "amount_cents": 100}

        def idem() -> dict[str, str]:
            return {"Idempotency-Key": str(uuid.uuid4())}

        check("outsider reads the bet", 404, client.get(bet_url, headers=bob))
        check("outsider cancels", 404, client.post(f"{bet_url}/cancel", headers=bob))
        check(
            "outsider proposes a result",
            404,
            client.post(f"{bet_url}/result", json={"outcome": "OUI"}, headers=bob),
        )
        check(
            "outsider contests",
            404,
            client.post(f"{bet_url}/contests", json={"reason": "x" * 20}, headers=bob),
        )
        check(
            "outsider creates an invitation",
            404,
            client.post(f"{bet_url}/invitations", headers=bob),
        )
        check(
            "outsider stakes",
            404,
            client.post(f"{bet_url}/stakes", json=stake, headers={**bob, **idem()}),
        )
        listing = client.get("/api/bets", headers=bob)
        hidden = all(item["id"] != bet["id"] for item in listing.json()["items"])
        check(
            "outsider list does not contain the bet",
            200,
            httpx.Response(200 if hidden else 500, request=listing.request),
        )
        check(
            "player reads moderation queue",
            403,
            client.get("/api/moderation/disputes", headers=bob),
        )
        check(
            "token with another sub",
            401,
            client.get("/api/me", headers=forge_subject(alice, str(uuid.uuid4()))),
        )

        invitation = client.post(f"{bet_url}/invitations", headers=alice).json()
        token = invitation["invitation_url"].rsplit("/", 1)[-1]
        check(
            "bob accepts the invitation",
            200,
            client.post("/api/invitations/accept", json={"token": token}, headers=bob),
        )
        check(
            "carol replays the used invitation",
            404,
            client.post("/api/invitations/accept", json={"token": token}, headers=carol),
        )
        check("participant reads the bet", 200, client.get(bet_url, headers=bob))
        check("participant cancels", 403, client.post(f"{bet_url}/cancel", headers=bob))
        check(
            "participant proposes a result",
            403,
            client.post(f"{bet_url}/result", json={"outcome": "OUI"}, headers=bob),
        )
        check(
            "participant creates an invitation",
            403,
            client.post(f"{bet_url}/invitations", headers=bob),
        )
        check(
            "participant stakes without balance",
            409,
            client.post(f"{bet_url}/stakes", json=stake, headers={**bob, **idem()}),
        )
        check(
            "bookie stakes on own bet",
            403,
            client.post(f"{bet_url}/stakes", json=stake, headers={**alice, **idem()}),
        )
        deposit = client.post(
            "/api/deposits", json={"amount_cents": 500}, headers={**alice, **idem()}
        ).json()
        deposit_url = f"/api/deposits/{deposit['deposit_id']}"
        check("owner reads the deposit", 200, client.get(deposit_url, headers=alice))
        check("other user reads the deposit", 404, client.get(deposit_url, headers=bob))
        check("logout", 204, client.post("/api/auth/logout", headers=alice))
        check("token after logout", 401, client.get("/api/me", headers=alice))

    failures = 0
    for label, method, expected, actual in results:
        verdict = "ok" if expected == actual else "ECART"
        failures += verdict != "ok"
        print(f"{verdict:5} {method:4} attendu {expected} obtenu {actual}  {label}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
