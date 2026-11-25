import json
import os
import secrets
import uuid
from itertools import count
from pathlib import Path
from typing import Any

from locust import between, task
from locust.contrib.fasthttp import FastHttpUser, FastResponse, ResponseContextManager
from locust.exception import StopUser

SEED = json.loads(Path(os.environ["TOUTBET_LOAD_SEED"]).read_text(encoding="utf-8"))
BET_ID: str = SEED["bet_id"]
NEXT_BETTOR = count()
STAKES_CENTS = (100, 200, 500, 1000)
EXPECTED_CONFLICTS = {"ALREADY_STAKED"}


def _error_code(response: FastResponse) -> str:
    try:
        code: str = response.json()["error"]["code"]
    except (ValueError, KeyError, TypeError):
        return "?"
    return code


def _judge(response: ResponseContextManager, *ok: int) -> bool:
    status = response.status_code
    if status in ok:
        response.success()
        return True
    if status == 0:
        response.failure("connexion perdue ou timeout")
    elif status == 429:
        response.failure("429 limitation de débit")
    elif status >= 500:
        response.failure(f"{status} erreur serveur")
    elif status == 409 and _error_code(response) in EXPECTED_CONFLICTS:
        response.success()
        return True
    else:
        response.failure(f"{status} {_error_code(response)}")
    return False


class Player(FastHttpUser):
    abstract = True
    wait_time = between(1, 3)

    def on_start(self) -> None:
        self.index = next(NEXT_BETTOR)
        if self.index >= len(SEED["bettors"]):
            raise StopUser
        self.email: str = SEED["bettors"][self.index]
        self.token: str | None = None
        self.staked = False

    def login(self) -> None:
        with self.client.post(
            "/api/auth/login",
            json={"email": self.email, "password": SEED["password"]},
            name="POST /api/auth/login",
            catch_response=True,
        ) as response:
            if _judge(response, 200):
                self.token = response.json()["access_token"]

    def _call(self, method: str, path: str, name: str, *ok: int, **kwargs: Any) -> bool:
        if self.token is None:
            self.login()
            return False
        headers = {"Authorization": f"Bearer {self.token}", **kwargs.pop("headers", {})}
        with self.client.request(
            method, path, name=name, headers=headers, catch_response=True, **kwargs
        ) as response:
            if response.status_code == 401:
                self.token = None
            return _judge(response, *ok)

    def read_bet(self) -> None:
        self._call("GET", f"/api/bets/{BET_ID}", "GET /api/bets/{id}", 200)

    def read_wallet(self) -> None:
        self._call("GET", "/api/wallet", "GET /api/wallet", 200)

    def stake_once(self) -> None:
        self.staked = self._call(
            "POST",
            f"/api/bets/{BET_ID}/stakes",
            "POST /api/bets/{id}/stakes",
            201,
            json={
                "outcome": secrets.choice(("OUI", "NON")),
                "amount_cents": secrets.choice(STAKES_CENTS),
            },
            headers={"Idempotency-Key": str(uuid.uuid4())},
        )


class Bettor(Player):
    def on_start(self) -> None:
        super().on_start()
        self.login()

    @task(5)
    def bet_detail(self) -> None:
        self.read_bet()

    @task(2)
    def wallet(self) -> None:
        self.read_wallet()

    @task(1)
    def my_bets(self) -> None:
        self._call("GET", "/api/bets", "GET /api/bets", 200)

    @task(3)
    def stake(self) -> None:
        if self.staked:
            self.read_bet()
        else:
            self.stake_once()


class StakeRush(Player):
    def on_start(self) -> None:
        super().on_start()
        self.token = SEED["tokens"][self.index]
        self.stake_once()

    @task
    def wallet(self) -> None:
        if self.staked:
            self.read_wallet()
        else:
            self.stake_once()
