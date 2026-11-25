import json
import re
import secrets
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import httpx
from playwright.sync_api import Locator, Page, Response, Route, expect
from sqlalchemy import update
from sqlalchemy.orm import Session

from toutbet.db import make_engine
from toutbet.models import Bet, Invitation
from toutbet.models.user import ROLE_MODERATOR, ROLE_USER
from toutbet.security.passwords import hash_password
from toutbet.security.webhook_signature import sign_webhook
from toutbet.services.accounts import create_user
from toutbet.services.invitations import hash_token

PASSWORD = "correct horse battery staple"
TOKEN_KEY = "toutbet.access_token"
PARIS = ZoneInfo("Europe/Paris")
BET_URL = re.compile(r"#/bets/([0-9a-f-]{36})$")
INVALID_DATA = "Données invalides."
NOT_FOUND = "Ressource introuvable."
FORBIDDEN = "Action non autorisée."
RATE_LIMITED = "Trop de requêtes, réessayez plus tard."
BAD_CREDENTIALS = "Authentification requise ou identifiants invalides."


@dataclass(frozen=True)
class Stack:
    base_url: str
    psp_url: str
    database_url: str
    webhook_secret: str


@dataclass(frozen=True)
class Account:
    email: str
    display_name: str
    password: str = PASSWORD


def new_account(prefix: str = "user") -> Account:
    suffix = secrets.token_hex(4)
    return Account(f"{prefix}-{suffix}@example.org", f"{prefix}_{suffix}")


def local_input(delta: timedelta) -> str:
    return (datetime.now(PARIS) + delta).strftime("%Y-%m-%dT%H:%M")


def euros_pattern(cents: int) -> re.Pattern[str]:
    whole = f"{cents // 100:,}".replace(",", r"\s")
    return re.compile(rf"^{whole},{cents % 100:02d}\s€$")


def tampered_json(changes: dict[str, Any], drop: tuple[str, ...] = ()) -> Callable[[Route], None]:
    def handler(route: Route) -> None:
        body = json.loads(route.request.post_data or "{}")
        body.update(changes)
        for name in drop:
            body.pop(name, None)
        route.continue_(post_data=json.dumps(body))

    return handler


@dataclass
class Phone:
    page: Page
    stack: Stack
    csp_violations: list[str] = field(default_factory=list)
    dialogs: list[str] = field(default_factory=list)
    requested_urls: list[str] = field(default_factory=list)

    @property
    def flash(self) -> Locator:
        return self.page.locator("#flash")

    @property
    def view(self) -> Locator:
        return self.page.locator("#view")

    def open(self, route: str = "") -> None:
        target = f"{self.stack.base_url}/{route}"
        if self.page.url == target:
            self.page.reload()
        else:
            self.page.goto(target)

    def follow_link(self, url: str) -> None:
        self.page.goto("about:blank")
        self.page.goto(url)

    def token(self) -> str | None:
        token: str | None = self.page.evaluate(f"sessionStorage.getItem('{TOKEN_KEY}')")
        return token

    def plant_token(self, token: str) -> None:
        self.page.evaluate(
            "([key, token]) => sessionStorage.setItem(key, token)", [TOKEN_KEY, token]
        )
        self.page.reload()

    def register(self, account: Account, *, birth_date: str = "1990-05-17") -> Response:
        page = self.page
        if not page.url.endswith("#/register"):
            self.open("#/register")
        expect(page.get_by_role("heading", name="Créer un compte")).to_be_visible()
        page.get_by_label("Email").fill(account.email)
        page.get_by_label("Pseudonyme").fill(account.display_name)
        page.get_by_label("Mot de passe (12 caractères minimum)").fill(account.password)
        page.get_by_label("Date de naissance").fill(birth_date)
        with page.expect_response(f"{self.stack.base_url}/api/auth/register") as info:
            page.get_by_role("button", name="Créer mon compte").click()
        if info.value.status == 202:
            expect(page.get_by_role("heading", name="Connexion")).to_be_visible()
        return info.value

    def submit_login(self, email: str, password: str) -> Response:
        page = self.page
        if not page.url.endswith("#/login"):
            self.open("#/login")
        expect(page.get_by_role("heading", name="Connexion")).to_be_visible()
        page.get_by_label("Email").fill(email)
        page.get_by_label("Mot de passe", exact=True).fill(password)
        with page.expect_response(f"{self.stack.base_url}/api/auth/login") as info:
            page.get_by_role("button", name="Se connecter").click()
        return info.value

    def login(self, account: Account) -> None:
        assert self.submit_login(account.email, account.password).status == 200
        expect(self.page.locator("#view h2").first).not_to_have_text("Connexion")

    def signup(self, account: Account) -> None:
        assert self.register(account).status == 202
        self.login(account)

    def logout(self) -> None:
        self.page.get_by_role("button", name="Déconnexion").click()
        expect(self.page.get_by_role("heading", name="Connexion")).to_be_visible()

    def expect_balance(self, cents: int) -> None:
        self.open("#/wallet")
        expect(self.page.locator(".amount")).to_have_text(euros_pattern(cents))

    def start_deposit(self, euros: str) -> dict[str, Any]:
        page = self.page
        created: dict[str, Any] = {}

        def capture(route: Route) -> None:
            response = route.fetch()
            created.update(response.json())
            route.fulfill(response=response)

        self.open("#/wallet")
        page.get_by_label("Montant à déposer (€)").fill(euros)
        page.route(f"{self.stack.base_url}/api/deposits", capture, times=1)
        page.get_by_role("button", name="Payer chez le PSP").click()
        page.wait_for_url(re.compile(re.escape(self.stack.psp_url) + r"/pay/"))
        return created

    def deposit(self, euros: str) -> dict[str, Any]:
        page = self.page
        created = self.start_deposit(euros)
        page.get_by_role("button", name="Payer", exact=True).click()
        expect(page.get_by_text("ToutBet' a été notifié.")).to_be_visible()
        page.get_by_role("link", name="Retour à ToutBet'").click()
        page.wait_for_url(f"{self.stack.base_url}/#/wallet")
        expect(page.locator(".amount")).to_be_visible()
        return created

    def verify_identity(self) -> None:
        page = self.page
        self.open("#/wallet")
        page.get_by_role("button", name="Vérifier mon identité").click()
        page.wait_for_url(re.compile(re.escape(self.stack.psp_url) + r"/kyc/"))
        page.get_by_role("button", name="Valider mon identité").click()
        expect(page.get_by_text("Identité vérifiée.")).to_be_visible()
        page.get_by_role("link", name="Retour à ToutBet'").click()
        page.wait_for_url(f"{self.stack.base_url}/#/wallet")
        expect(page.get_by_role("button", name="Retirer vers mon compte vérifié")).to_be_visible()

    def fill_new_bet(
        self,
        title: str,
        *,
        description: str = "Pari entre amis.",
        commission: str = "4",
        closes_in: timedelta = timedelta(hours=1),
        starts_in: timedelta = timedelta(hours=2),
    ) -> None:
        page = self.page
        self.open("#/bets/new")
        expect(page.get_by_role("heading", name="Nouveau pari")).to_be_visible()
        page.get_by_label("Titre", exact=True).fill(title)
        page.get_by_label("Description", exact=True).fill(description)
        page.get_by_label("Début de l'événement").fill(local_input(starts_in))
        page.get_by_label("Clôture des mises").fill(local_input(closes_in))
        page.get_by_label("Commission bookie (%)").fill(commission)

    def submit_new_bet(self) -> Response:
        with self.page.expect_response(f"{self.stack.base_url}/api/bets") as info:
            self.page.get_by_role("button", name="Créer le pari").click()
        return info.value

    def create_bet(self, title: str, **options: Any) -> str:
        self.fill_new_bet(title, **options)
        assert self.submit_new_bet().status == 201
        self.page.wait_for_url(BET_URL)
        match = BET_URL.search(self.page.url)
        assert match is not None
        return match.group(1)

    def invite(self, bet_id: str) -> str:
        self.open(f"#/bets/{bet_id}")
        self.page.get_by_role("button", name="Créer un lien d'invitation").click()
        link = self.page.locator("p.token")
        expect(link).to_be_visible()
        return link.inner_text()

    def join(self, url: str) -> str:
        self.follow_link(url)
        self.page.wait_for_url(BET_URL)
        match = BET_URL.search(self.page.url)
        assert match is not None
        return match.group(1)

    def fill_stake(self, bet_id: str, outcome: str, euros: str) -> None:
        self.open(f"#/bets/{bet_id}")
        self.page.get_by_label("Issue").select_option(outcome)
        self.page.get_by_label("Mise (€)").fill(euros)

    def submit_stake(self) -> Response:
        page = self.page
        with page.expect_response(
            lambda r: r.request.method == "POST" and r.url.endswith("/stakes")
        ) as info:
            page.get_by_role("button", name="Miser").click()
        return info.value

    def stake(self, bet_id: str, outcome: str, euros: str) -> Response:
        self.fill_stake(bet_id, outcome, euros)
        return self.submit_stake()

    def fetch(
        self, method: str, path: str, body: Any = None, headers: dict[str, str] | None = None
    ) -> dict[str, Any]:
        raw = body if body is None or isinstance(body, str) else json.dumps(body)
        result: dict[str, Any] = self.page.evaluate(
            """async ([method, path, body, extra, key]) => {
                const headers = { "Content-Type": "application/json", ...extra };
                const token = sessionStorage.getItem(key);
                if (token) headers.Authorization = `Bearer ${token}`;
                const response = await fetch(path, { method, headers, body: body ?? undefined });
                return {
                    status: response.status,
                    text: await response.text(),
                    headers: Object.fromEntries(response.headers.entries()),
                };
            }""",
            [method, path, raw, headers or {}, TOKEN_KEY],
        )
        return result


class Api:
    def __init__(self, stack: Stack) -> None:
        self.stack = stack
        self.client = httpx.Client(base_url=stack.base_url, timeout=15)

    def close(self) -> None:
        self.client.close()

    @staticmethod
    def auth(token: str) -> dict[str, str]:
        return {"Authorization": f"Bearer {token}"}

    def register(self, account: Account) -> None:
        response = self.client.post(
            "/api/auth/register",
            json={
                "email": account.email,
                "display_name": account.display_name,
                "password": account.password,
                "birth_date": "1990-05-17",
            },
        )
        assert response.status_code == 202, response.text

    def login(self, account: Account) -> str:
        response = self.client.post(
            "/api/auth/login", json={"email": account.email, "password": account.password}
        )
        assert response.status_code == 200, response.text
        token: str = response.json()["access_token"]
        return token

    def signup(self, account: Account) -> str:
        self.register(account)
        return self.login(account)

    def get(self, path: str, token: str) -> httpx.Response:
        return self.client.get(path, headers=self.auth(token))

    def post(
        self, path: str, token: str, body: Any = None, headers: dict[str, str] | None = None
    ) -> httpx.Response:
        return self.client.post(path, json=body, headers={**self.auth(token), **(headers or {})})

    def me(self, token: str) -> dict[str, Any]:
        response = self.get("/api/me", token)
        assert response.status_code == 200, response.text
        me: dict[str, Any] = response.json()
        return me

    def balance(self, token: str) -> int:
        response = self.get("/api/wallet", token)
        assert response.status_code == 200, response.text
        balance: int = response.json()["balance_cents"]
        return balance

    def fund(self, token: str, cents: int) -> None:
        response = self.post(
            "/api/deposits",
            token,
            {"amount_cents": cents},
            {"Idempotency-Key": str(uuid.uuid4())},
        )
        assert response.status_code == 201, response.text
        session_id = response.json()["redirect_url"].rsplit("/", 1)[1]
        paid = httpx.post(f"{self.stack.psp_url}/pay/{session_id}/confirm", timeout=15)
        assert "a été notifié." in paid.text

    def create_bet(self, token: str, title: str = "Matis va-t-il réussir son créneau ?") -> str:
        now = datetime.now(UTC)
        response = self.post(
            "/api/bets",
            token,
            {
                "title": title,
                "closes_at": (now + timedelta(hours=1)).isoformat(),
                "event_starts_at": (now + timedelta(hours=2)).isoformat(),
                "bookie_commission_bps": 400,
            },
        )
        assert response.status_code == 201, response.text
        bet_id: str = response.json()["id"]
        return bet_id

    def invitation_url(self, token: str, bet_id: str) -> str:
        response = self.post(f"/api/bets/{bet_id}/invitations", token)
        assert response.status_code == 201, response.text
        url: str = response.json()["invitation_url"]
        return url

    def join(self, token: str, url: str) -> None:
        response = self.post("/api/invitations/accept", token, {"token": url.rsplit("/", 1)[1]})
        assert response.status_code == 200, response.text

    def stake(self, token: str, bet_id: str, outcome: str, cents: int) -> httpx.Response:
        return self.post(
            f"/api/bets/{bet_id}/stakes",
            token,
            {"outcome": outcome, "amount_cents": cents},
            {"Idempotency-Key": str(uuid.uuid4())},
        )

    def bet(self, token: str, bet_id: str) -> dict[str, Any]:
        response = self.get(f"/api/bets/{bet_id}", token)
        assert response.status_code == 200, response.text
        bet: dict[str, Any] = response.json()
        return bet

    def signed_webhook(self, event: dict[str, Any], timestamp: int) -> tuple[bytes, str]:
        body = json.dumps(event).encode()
        return body, sign_webhook(self.stack.webhook_secret.encode(), timestamp, body)


@contextmanager
def _database(stack: Stack) -> Iterator[Session]:
    engine = make_engine(stack.database_url)
    try:
        with Session(engine) as db, db.begin():
            yield db
    finally:
        engine.dispose()


def close_betting(stack: Stack, bet_id: str) -> None:
    past = datetime.now(UTC) - timedelta(minutes=1)
    with _database(stack) as db:
        db.execute(update(Bet).where(Bet.id == uuid.UUID(bet_id)).values(closes_at=past))


def start_event(stack: Stack, bet_id: str) -> None:
    now = datetime.now(UTC)
    with _database(stack) as db:
        db.execute(
            update(Bet)
            .where(Bet.id == uuid.UUID(bet_id))
            .values(
                status="closed",
                closes_at=now - timedelta(minutes=2),
                event_starts_at=now - timedelta(minutes=1),
            )
        )


def expire_invitation(stack: Stack, url: str) -> None:
    token_hash = hash_token(url.rsplit("/", 1)[1])
    past = datetime.now(UTC) - timedelta(minutes=1)
    with _database(stack) as db:
        db.execute(
            update(Invitation).where(Invitation.token_hash == token_hash).values(expires_at=past)
        )


def seed_account(stack: Stack, account: Account, *, moderator: bool = False) -> None:
    with _database(stack) as db:
        create_user(
            db,
            email=account.email,
            display_name=account.display_name,
            password_hash=hash_password(account.password),
            birth_date=date(1985, 1, 31),
            role=ROLE_MODERATOR if moderator else ROLE_USER,
            now=datetime.now(UTC),
        )
