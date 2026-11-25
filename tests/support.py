import base64
import json
import secrets
import threading
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any

from fastapi.testclient import TestClient
from httpx import Response
from sqlalchemy.orm import Session

from toutbet.models.user import ROLE_MODERATOR
from toutbet.psp.client import HostedSession, Payout, PayoutStatus, PspRefusedError
from toutbet.security.passwords import hash_password
from toutbet.security.webhook_signature import sign_webhook
from toutbet.services.accounts import create_user
from toutbet.worker import TickReport, tick

DEFAULT_PASSWORD = "correct horse battery staple"


def random_seed() -> str:
    return base64.urlsafe_b64encode(secrets.token_bytes(32)).rstrip(b"=").decode()


class FakeClock:
    def __init__(self, start: datetime) -> None:
        self._now = start
        self._lock = threading.Lock()

    def now(self) -> datetime:
        with self._lock:
            return self._now

    def advance(self, delta: timedelta) -> None:
        with self._lock:
            self._now += delta

    def set(self, moment: datetime) -> None:
        with self._lock:
            self._now = moment


@dataclass
class FakePsp:
    checkouts: dict[str, dict[str, Any]] = field(default_factory=dict)
    payouts: list[dict[str, Any]] = field(default_factory=list)
    payout_statuses: dict[str, PayoutStatus] = field(default_factory=dict)
    kyc_sessions: list[dict[str, Any]] = field(default_factory=list)
    checkout_error: Exception | None = None
    payout_error: Exception | None = None
    payout_reply_error: Exception | None = None
    lookup_error: Exception | None = None
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def create_checkout(
        self, *, reference: str, amount_cents: int, currency: str, return_url: str
    ) -> HostedSession:
        if self.checkout_error is not None:
            raise self.checkout_error
        session_id = f"cs_{uuid.uuid4().hex}"
        with self._lock:
            self.checkouts[reference] = {
                "session_id": session_id,
                "amount_cents": amount_cents,
                "currency": currency,
                "return_url": return_url,
            }
        return HostedSession(session_id, f"https://psp.test/pay/{session_id}")

    def create_kyc_session(self, *, reference: str, return_url: str) -> HostedSession:
        session_id = f"kyc_{uuid.uuid4().hex}"
        with self._lock:
            self.kyc_sessions.append({"reference": reference, "session_id": session_id})
        return HostedSession(session_id, f"https://psp.test/kyc/{session_id}")

    def create_payout(
        self, *, reference: str, user_reference: str, amount_cents: int, currency: str
    ) -> Payout:
        if self.payout_error is not None:
            raise self.payout_error
        with self._lock:
            status = self.payout_statuses.get(reference)
            if status is None:
                status = PayoutStatus(f"po_{uuid.uuid4().hex}", "paid")
                self.payout_statuses[reference] = status
                self.payouts.append(
                    {"reference": reference, "user": user_reference, "amount_cents": amount_cents}
                )
        if self.payout_reply_error is not None:
            raise self.payout_reply_error
        if status.status == "refused":
            raise PspRefusedError("status 402")
        return Payout(status.payout_id)

    def find_payout(self, *, reference: str) -> PayoutStatus | None:
        if self.lookup_error is not None:
            raise self.lookup_error
        with self._lock:
            return self.payout_statuses.get(reference)


@dataclass(eq=False)
class Player:
    id: str
    email: str
    display_name: str
    password: str
    token: str

    @property
    def headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.token}"}


def idempotency_headers(player: Player, key: str | None = None) -> dict[str, str]:
    return {**player.headers, "Idempotency-Key": key or str(uuid.uuid4())}


class Api:
    def __init__(self, client: TestClient, clock: FakeClock, psp: FakePsp) -> None:
        self.client = client
        self.clock = clock
        self.psp = psp

    @property
    def settings(self) -> Any:
        return self.client.app.state.settings

    def tick(self) -> TickReport:
        factory = self.client.app.state.session_factory
        with factory() as session:
            return tick(session, self.clock, self.psp)

    def start_event(self, bet: dict[str, Any], *players: Player) -> None:
        moment = datetime.fromisoformat(bet["event_starts_at"])
        self.clock.advance(moment - self.clock.now() + timedelta(seconds=1))
        self.tick()
        self.relogin(*players)

    def end_contest_window(self, *players: Player) -> None:
        self.clock.advance(timedelta(hours=24, seconds=1))
        self.tick()
        self.relogin(*players)

    @contextmanager
    def db(self) -> Iterator[Session]:
        factory = self.client.app.state.session_factory
        with factory() as session, session.begin():
            yield session

    def register(self, **overrides: Any) -> Response:
        suffix = secrets.token_hex(4)
        body = {
            "email": f"user-{suffix}@example.org",
            "display_name": f"user_{suffix}",
            "password": DEFAULT_PASSWORD,
            "birth_date": "1990-05-17",
            **overrides,
        }
        return self.client.post("/api/auth/register", json=body)

    def login(self, email: str, password: str = DEFAULT_PASSWORD) -> Response:
        return self.client.post("/api/auth/login", json={"email": email, "password": password})

    def signup(self, display_name: str | None = None) -> Player:
        suffix = secrets.token_hex(4)
        name = display_name or f"user_{suffix}"
        email = f"{name.lower()}-{suffix}@example.org"
        assert self.register(email=email, display_name=name).status_code == 202
        return self._session_for(email, name, DEFAULT_PASSWORD)

    def _session_for(self, email: str, name: str, password: str) -> Player:
        response = self.login(email, password)
        assert response.status_code == 200, response.text
        token = response.json()["access_token"]
        me = self.client.get("/api/me", headers={"Authorization": f"Bearer {token}"}).json()
        return Player(me["id"], email, name, password, token)

    def relogin(self, *players: Player) -> None:
        for player in players:
            response = self.login(player.email, player.password)
            assert response.status_code == 200, response.text
            player.token = response.json()["access_token"]

    def moderator(self) -> Player:
        suffix = secrets.token_hex(4)
        email = f"mod-{suffix}@toutbet.test"
        name = f"mod_{suffix}"
        with self.db() as session:
            create_user(
                session,
                email=email,
                display_name=name,
                password_hash=hash_password(DEFAULT_PASSWORD),
                birth_date=date(1985, 1, 1),
                role=ROLE_MODERATOR,
                now=self.clock.now(),
            )
        return self._session_for(email, name, DEFAULT_PASSWORD)

    def signed_webhook(
        self, payload: dict[str, Any], timestamp: int | None = None, secret: str | None = None
    ) -> Response:
        body = json.dumps(payload).encode()
        key = (secret or self.settings.psp_webhook_secret.get_secret_value()).encode()
        moment = int(self.clock.now().timestamp()) if timestamp is None else timestamp
        header = sign_webhook(key, moment, body)
        return self.client.post(
            "/api/webhooks/psp",
            content=body,
            headers={"Content-Type": "application/json", "X-PSP-Signature": header},
        )

    def start_deposit(self, player: Player, amount_cents: int) -> Response:
        return self.client.post(
            "/api/deposits",
            json={"amount_cents": amount_cents},
            headers=idempotency_headers(player),
        )

    def payment_event(self, deposit_id: str, **overrides: Any) -> dict[str, Any]:
        checkout = self.psp.checkouts[deposit_id]
        data = {
            "session_id": checkout["session_id"],
            "amount_cents": checkout["amount_cents"],
            "currency": checkout["currency"],
        }
        data.update(overrides)
        return {"event_id": f"evt_{uuid.uuid4().hex}", "type": "payment.succeeded", "data": data}

    def fund(self, player: Player, amount_cents: int) -> None:
        response = self.start_deposit(player, amount_cents)
        assert response.status_code == 201, response.text
        event = self.payment_event(response.json()["deposit_id"])
        assert self.signed_webhook(event).status_code == 200

    def verify_kyc(self, player: Player) -> None:
        event = {
            "event_id": f"evt_{uuid.uuid4().hex}",
            "type": "kyc.verified",
            "data": {"reference": player.id},
        }
        assert self.signed_webhook(event).status_code == 200

    def balance(self, player: Player) -> int:
        response = self.client.get("/api/wallet", headers=player.headers)
        assert response.status_code == 200, response.text
        balance: int = response.json()["balance_cents"]
        return balance

    def bet_body(self, **overrides: Any) -> dict[str, Any]:
        now = self.clock.now()
        return {
            "title": "Matis va-t-il réussir son créneau ?",
            "description": "Pari entre amis.",
            "closes_at": (now + timedelta(hours=1)).isoformat(),
            "event_starts_at": (now + timedelta(hours=2)).isoformat(),
            "bookie_commission_bps": 400,
            **overrides,
        }

    def create_bet(self, bookie: Player, **overrides: Any) -> dict[str, Any]:
        response = self.client.post(
            "/api/bets", json=self.bet_body(**overrides), headers=bookie.headers
        )
        assert response.status_code == 201, response.text
        bet: dict[str, Any] = response.json()
        return bet

    def invite(self, bookie: Player, bet_id: str) -> str:
        response = self.client.post(f"/api/bets/{bet_id}/invitations", headers=bookie.headers)
        assert response.status_code == 201, response.text
        url: str = response.json()["invitation_url"]
        return url.rsplit("/", 1)[1]

    def join(self, player: Player, token: str) -> Response:
        return self.client.post(
            "/api/invitations/accept", json={"token": token}, headers=player.headers
        )

    def add_participants(self, bookie: Player, bet_id: str, *players: Player) -> None:
        for player in players:
            assert self.join(player, self.invite(bookie, bet_id)).status_code == 200

    def stake(
        self, player: Player, bet_id: str, outcome: str, amount_cents: int, key: str | None = None
    ) -> Response:
        return self.client.post(
            f"/api/bets/{bet_id}/stakes",
            json={"outcome": outcome, "amount_cents": amount_cents},
            headers=idempotency_headers(player, key),
        )

    def bet(self, player: Player, bet_id: str) -> dict[str, Any]:
        response = self.client.get(f"/api/bets/{bet_id}", headers=player.headers)
        assert response.status_code == 200, response.text
        bet: dict[str, Any] = response.json()
        return bet

    def propose(
        self, bookie: Player, bet_id: str, outcome: str, note: str | None = None
    ) -> Response:
        body: dict[str, Any] = {"outcome": outcome}
        if note is not None:
            body["note"] = note
        return self.client.post(f"/api/bets/{bet_id}/result", json=body, headers=bookie.headers)

    def contest(
        self, player: Player, bet_id: str, reason: str = "Le résultat est faux."
    ) -> Response:
        return self.client.post(
            f"/api/bets/{bet_id}/contests", json={"reason": reason}, headers=player.headers
        )

    def decide(
        self, moderator: Player, bet_id: str, outcome: str, justification: str | None = None
    ) -> Response:
        body = {
            "outcome": outcome,
            "justification": justification or "Vidéo de l'événement consultée, résultat clair.",
        }
        return self.client.post(
            f"/api/moderation/bets/{bet_id}/decision", json=body, headers=moderator.headers
        )


@dataclass
class Scenario:
    bookie: Player
    bet: dict[str, Any]
    players: dict[str, Player]

    @property
    def everyone(self) -> list[Player]:
        return [self.bookie, *self.players.values()]


def staked_bet(api: Api, stakes: list[tuple[str, str, int]], **bet_overrides: Any) -> Scenario:
    bookie = api.signup("bookie")
    bet = api.create_bet(bookie, **bet_overrides)
    players: dict[str, Player] = {}
    for name, outcome, amount in stakes:
        player = api.signup(name)
        api.fund(player, max(amount, 500))
        api.add_participants(bookie, bet["id"], player)
        response = api.stake(player, bet["id"], outcome, amount)
        assert response.status_code == 201, response.text
        players[name] = player
        api.clock.advance(timedelta(seconds=1))
    return Scenario(bookie, bet, players)


def proposed_bet(
    api: Api, stakes: list[tuple[str, str, int]], outcome: str, **bet_overrides: Any
) -> Scenario:
    scenario = staked_bet(api, stakes, **bet_overrides)
    api.start_event(scenario.bet, *scenario.everyone)
    response = api.propose(scenario.bookie, scenario.bet["id"], outcome)
    assert response.status_code == 200, response.text
    return scenario
