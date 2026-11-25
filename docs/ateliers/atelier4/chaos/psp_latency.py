import argparse
import json
import secrets
import statistics
import threading
import time
import uuid
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from toutbet.models import Deposit, LedgerEntry, Withdrawal

PASSWORD_BYTES = 18
FUNDS_CENTS = 5000
FORGED_TOKEN = secrets.token_urlsafe(32)


@dataclass
class Sample:
    name: str
    at: float
    ms: float
    status: int
    code: str


class Driver:
    def __init__(self, host: str, psp: str) -> None:
        limits = httpx.Limits(max_connections=300, max_keepalive_connections=300)
        self.client = httpx.Client(base_url=host, timeout=60, limits=limits)
        self.psp = psp
        self.started = time.monotonic()
        self.samples: list[Sample] = []
        self.lock = threading.Lock()

    def call(
        self,
        name: str,
        method: str,
        path: str,
        *,
        token: str | None = None,
        body: Any = None,
        key: str | None = None,
        content: bytes | None = None,
        extra_headers: dict[str, str] | None = None,
    ) -> httpx.Response:
        headers = dict(extra_headers or {})
        if token:
            headers["Authorization"] = f"Bearer {token}"
        if key:
            headers["Idempotency-Key"] = key
        begin = time.monotonic()
        response = self.client.request(method, path, json=body, content=content, headers=headers)
        elapsed = (time.monotonic() - begin) * 1000
        try:
            code = str(response.json().get("error", {}).get("code", ""))
        except (ValueError, AttributeError):
            code = ""
        with self.lock:
            self.samples.append(
                Sample(
                    name,
                    round(begin - self.started, 2),
                    round(elapsed, 1),
                    response.status_code,
                    code,
                )
            )
        return response

    def expect(self, response: httpx.Response, status: int) -> dict[str, Any]:
        if response.status_code != status:
            raise RuntimeError(
                f"{response.request.url.path}: {response.status_code} {response.text}"
            )
        body: dict[str, Any] = response.json()
        return body

    def login(self, email: str, password: str) -> str:
        response = self.call(
            "login", "POST", "/api/auth/login", body={"email": email, "password": password}
        )
        token: str = self.expect(response, 200)["access_token"]
        return token

    def register(self, email: str, password: str) -> str:
        body = {
            "email": email,
            "display_name": email.split("@", maxsplit=1)[0].replace("-", "_"),
            "password": password,
            "birth_date": "1990-05-17",
        }
        self.expect(self.call("register", "POST", "/api/auth/register", body=body), 202)
        return self.login(email, password)

    def deposit(self, token: str, cents: int) -> httpx.Response:
        return self.call(
            "deposit",
            "POST",
            "/api/deposits",
            token=token,
            body={"amount_cents": cents},
            key=str(uuid.uuid4()),
        )

    def pay(self, token: str, cents: int) -> None:
        session = self.expect(self.deposit(token, cents), 201)["redirect_url"].rsplit("/", 1)[1]
        if "a été notifié" not in httpx.post(f"{self.psp}/pay/{session}/confirm", timeout=30).text:
            raise RuntimeError("mock PSP did not notify the payment")

    def verify_kyc(self, token: str) -> None:
        started = self.expect(self.call("kyc", "POST", "/api/kyc/start", token=token), 200)
        session = started["redirect_url"].rsplit("/", 1)[1]
        if (
            "Identité vérifiée"
            not in httpx.post(f"{self.psp}/kyc/{session}/verify", timeout=30).text
        ):
            raise RuntimeError("mock PSP did not verify the identity")

    def withdraw(
        self, token: str, password: str, cents: int, key: str | None = None
    ) -> httpx.Response:
        body = {"amount_cents": cents, "password": password}
        return self.call(
            "withdrawal",
            "POST",
            "/api/withdrawals",
            token=token,
            body=body,
            key=key or str(uuid.uuid4()),
        )

    def stake(self, token: str, bet_id: str) -> httpx.Response:
        body = {"outcome": "OUI", "amount_cents": 100}
        return self.call(
            "stake",
            "POST",
            f"/api/bets/{bet_id}/stakes",
            token=token,
            body=body,
            key=str(uuid.uuid4()),
        )


def seed(args: argparse.Namespace) -> None:
    driver = Driver(args.host, args.psp)
    run = secrets.token_hex(3)
    password = secrets.token_urlsafe(PASSWORD_BYTES)
    bookie = driver.register(f"bookie-{run}@example.org", password)
    now = datetime.now(UTC)
    bet = {
        "title": "Matis va-t-il réussir son créneau ?",
        "closes_at": (now + timedelta(hours=6)).isoformat(),
        "event_starts_at": (now + timedelta(hours=7)).isoformat(),
        "bookie_commission_bps": 300,
    }
    bet_id = driver.expect(driver.call("bet", "POST", "/api/bets", token=bookie, body=bet), 201)[
        "id"
    ]
    emails = [f"chaos{i}-{run}@example.org" for i in range(args.players)]

    def player(email: str) -> None:
        token = driver.register(email, password)
        driver.pay(token, FUNDS_CENTS)
        driver.verify_kyc(token)
        invitation = driver.expect(
            driver.call("invite", "POST", f"/api/bets/{bet_id}/invitations", token=bookie), 201
        )
        token_part = invitation["invitation_url"].rsplit("/", 1)[1]
        driver.expect(
            driver.call(
                "join",
                "POST",
                "/api/invitations/accept",
                token=token,
                body={"token": token_part},
            ),
            200,
        )

    with ThreadPoolExecutor(8) as pool:
        list(pool.map(player, emails))
    state = {"password": password, "bet_id": bet_id, "players": emails}
    args.state.write_text(json.dumps(state, indent=2), encoding="utf-8")
    print(f"seeded {len(emails)} players, bet {bet_id}")


def probe(driver: Driver, token: str, bet_id: str, stop: threading.Event) -> None:
    while not stop.is_set():
        driver.call("healthz", "GET", "/healthz")
        driver.call("wallet", "GET", "/api/wallet", token=token)
        driver.call("bet_view", "GET", f"/api/bets/{bet_id}", token=token)
        driver.call("bad_token", "GET", "/api/wallet", token=FORGED_TOKEN)
        driver.call(
            "bad_webhook",
            "POST",
            "/api/webhooks/psp",
            content=b"{}",
            extra_headers={"Content-Type": "application/json", "X-PSP-Signature": "t=0,v1=00"},
        )
        stop.wait(0.5)


def tokens_for(driver: Driver, state: dict[str, Any]) -> list[str]:
    with ThreadPoolExecutor(8) as pool:
        return list(
            pool.map(lambda email: driver.login(email, state["password"]), state["players"])
        )


def steady(args: argparse.Namespace) -> None:
    state = json.loads(args.state.read_text(encoding="utf-8"))
    driver = Driver(args.host, args.psp)
    tokens = tokens_for(driver, state)
    stop = threading.Event()
    prober = threading.Thread(target=probe, args=(driver, tokens[0], state["bet_id"], stop))
    prober.start()
    deadline = time.monotonic() + args.seconds
    turn = 0
    while time.monotonic() < deadline:
        token = tokens[turn % len(tokens)]
        driver.deposit(token, 500)
        driver.withdraw(token, state["password"], 1000)
        turn += 1
        time.sleep(5)
    stop.set()
    prober.join()
    report(driver, args.out)


def burst(args: argparse.Namespace) -> None:
    state = json.loads(args.state.read_text(encoding="utf-8"))
    driver = Driver(args.host, args.psp)
    tokens = tokens_for(driver, state)
    stop = threading.Event()
    prober = threading.Thread(target=probe, args=(driver, tokens[0], state["bet_id"], stop))
    prober.start()
    time.sleep(3)
    password = state["password"]
    replay_key = str(uuid.uuid4())
    jobs: list[tuple[str, int]] = [(token, 0) for token in tokens]
    jobs += [(token, 1) for token in tokens for _ in range(args.deposits_per_player)]

    def run(job: tuple[str, int]) -> None:
        token, kind = job
        if kind == 0:
            key = replay_key if token == tokens[0] else None
            driver.withdraw(token, password, 1000, key)
        else:
            driver.deposit(token, 1000)

    with ThreadPoolExecutor(len(jobs)) as pool:
        futures = [pool.submit(run, job) for job in jobs]
        time.sleep(1)
        for token in tokens[1 : 1 + args.stakers]:
            driver.stake(token, state["bet_id"])
        time.sleep(1)
        driver.call(
            "replay",
            "POST",
            "/api/withdrawals",
            token=tokens[0],
            body={"amount_cents": 1000, "password": password},
            key=replay_key,
        )
        for future in futures:
            future.result()
    time.sleep(3)
    stop.set()
    prober.join()
    report(driver, args.out)


def summarize(samples: list[Sample]) -> list[str]:
    by_name: dict[str, list[Sample]] = defaultdict(list)
    for sample in samples:
        by_name[sample.name].append(sample)
    lines = []
    for name, group in sorted(by_name.items()):
        durations = sorted(s.ms for s in group)
        p95 = durations[max(0, int(len(durations) * 0.95) - 1)]
        statuses = Counter(f"{s.status}{' ' + s.code if s.code else ''}" for s in group)
        lines.append(
            f"{name:11} n={len(group):4} p50={statistics.median(durations):8.1f} ms "
            f"p95={p95:8.1f} ms max={durations[-1]:8.1f} ms  {dict(statuses)}"
        )
    return lines


def report(driver: Driver, out: Path) -> None:
    lines = summarize(driver.samples)
    out.write_text(json.dumps([asdict(s) for s in driver.samples]), encoding="utf-8")
    out.with_suffix(".txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))


def verify(args: argparse.Namespace) -> None:
    payouts: Counter[str] = Counter()
    for line in args.psp_log.read_text(encoding="utf-8").splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if event.get("event") == "mock_psp.payout":
            payouts[event["reference"]] += 1
    engine = create_engine(args.database_url, hide_parameters=True)
    with Session(engine) as db:
        withdrawals = db.scalars(select(Withdrawal)).all()
        deposits = db.execute(select(Deposit.status, func.count()).group_by(Deposit.status)).all()
        entries = db.execute(
            select(LedgerEntry.entry_type, func.count()).group_by(LedgerEntry.entry_type)
        ).all()
        debits = Counter(
            db.scalars(
                select(LedgerEntry.idempotency_key).where(LedgerEntry.entry_type == "withdrawal")
            )
        )
    engine.dispose()
    statuses = Counter(w.status for w in withdrawals)
    paid_twice = [ref for ref, count in payouts.items() if count > 1]
    sent_without_payout = [
        str(w.id) for w in withdrawals if w.status == "sent" and payouts[str(w.id)] == 0
    ]
    print(f"withdrawals by status: {dict(statuses)}")
    print(f"deposits by status: {dict(deposits)}")
    print(f"ledger entries by type: {dict(entries)}")
    print(f"mock PSP payouts: {sum(payouts.values())} for {len(payouts)} references")
    print(f"references paid more than once: {paid_twice}")
    print(f"withdrawals marked sent without a PSP payout: {sent_without_payout}")
    print(f"withdrawal debits: {sum(debits.values())} for {len(withdrawals)} withdrawals")
    print(f"idempotency keys debited more than once: {[k for k, c in debits.items() if c > 1]}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Chaos run: PSP latency on deposits and withdrawals."
    )
    parser.add_argument("--host", default="http://127.0.0.1:8300")
    parser.add_argument("--psp", default="http://127.0.0.1:8301")
    parser.add_argument("--state", type=Path, required=True)
    commands = parser.add_subparsers(dest="command", required=True)
    seeding = commands.add_parser("seed")
    seeding.add_argument("--players", type=int, default=12)
    seeding.set_defaults(handler=seed)
    steady_state = commands.add_parser("steady")
    steady_state.add_argument("--seconds", type=int, default=60)
    steady_state.add_argument("--out", type=Path, required=True)
    steady_state.set_defaults(handler=steady)
    bursting = commands.add_parser("burst")
    bursting.add_argument("--deposits-per-player", type=int, default=4)
    bursting.add_argument("--stakers", type=int, default=6)
    bursting.add_argument("--out", type=Path, required=True)
    bursting.set_defaults(handler=burst)
    checking = commands.add_parser("verify")
    checking.add_argument("--database-url", required=True)
    checking.add_argument("--psp-log", type=Path, required=True)
    checking.set_defaults(handler=verify)
    args = parser.parse_args()
    args.handler(args)


if __name__ == "__main__":
    main()
