import re
import secrets
import statistics
import sys
import time

import httpx

BASE_URL = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8100"
ROUNDS = int(sys.argv[2]) if len(sys.argv) > 2 else 30


def timed(client: httpx.Client, path: str, body: dict[str, str]) -> tuple[float, int, str]:
    started = time.perf_counter()
    response = client.post(path, json=body)
    elapsed = (time.perf_counter() - started) * 1000
    return elapsed, response.status_code, re.sub(r'"request_id":"\w+"', "", response.text)


def register_body(email: str) -> dict[str, str]:
    return {
        "email": email,
        "display_name": f"t_{secrets.token_hex(6)}",
        "password": secrets.token_urlsafe(16),
        "birth_date": "1990-01-01",
    }


def summary(label: str, samples: list[tuple[float, int, str]]) -> None:
    durations = sorted(sample[0] for sample in samples)
    statuses = sorted({sample[1] for sample in samples})
    bodies = {sample[2] for sample in samples}
    p90 = durations[int(len(durations) * 0.9) - 1]
    print(
        f"{label:40} n={len(samples):3} statuts={statuses} corps_distincts={len(bodies)} "
        f"mediane={statistics.median(durations):7.1f} ms p90={p90:7.1f} ms"
    )


def main() -> int:
    known = f"known-{secrets.token_hex(4)}@dast.test"
    with httpx.Client(base_url=BASE_URL, timeout=30) as client:
        client.post("/api/auth/register", json=register_body(known))
        login_known: list[tuple[float, int, str]] = []
        login_unknown: list[tuple[float, int, str]] = []
        register_new: list[tuple[float, int, str]] = []
        register_existing: list[tuple[float, int, str]] = []
        for _ in range(ROUNDS):
            wrong = {"password": secrets.token_urlsafe(12)}
            login_known.append(timed(client, "/api/auth/login", {"email": known, **wrong}))
            unknown = f"nobody-{secrets.token_hex(6)}@dast.test"
            login_unknown.append(timed(client, "/api/auth/login", {"email": unknown, **wrong}))
        for _ in range(ROUNDS // 2):
            new = f"new-{secrets.token_hex(6)}@dast.test"
            register_new.append(timed(client, "/api/auth/register", register_body(new)))
            register_existing.append(timed(client, "/api/auth/register", register_body(known)))
    summary("login, email connu, mauvais mot de passe", login_known)
    summary("login, email inconnu", login_unknown)
    summary("inscription, email nouveau", register_new)
    summary("inscription, email deja pris", register_existing)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
