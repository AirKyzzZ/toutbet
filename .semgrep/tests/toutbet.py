import uuid
from decimal import Decimal

from fastapi import APIRouter, Depends, FastAPI
from sqlalchemy import Connection, text, update
from sqlalchemy.orm import Mapped, Session

from toutbet.api.deps import protected_router
from toutbet.models import Wallet
from toutbet.security.authz import AuthenticatedUser, Moderator, Player, current_user
from toutbet.services.money import transfer


def credit_bonus(db: Session, wallet: Wallet, user_id: uuid.UUID) -> None:
    # ruleid: toutbet-balance-write-outside-money
    wallet.balance_cents = 1_000
    # ruleid: toutbet-balance-write-outside-money
    wallet.balance_cents += 500
    # ruleid: toutbet-balance-write-outside-money
    wallet.balance_cents -= 500
    # ruleid: toutbet-balance-write-outside-money
    db.execute(update(Wallet).where(Wallet.user_id == user_id).values(balance_cents=99_999))
    # ruleid: toutbet-balance-write-outside-money
    db.execute(update(Wallet).values({"balance_cents": 1}))
    # ruleid: toutbet-balance-write-outside-money
    db.add(Wallet(user_id=user_id, balance_cents=10_000))
    # ruleid: toutbet-balance-write-outside-money
    db.execute(text("UPDATE wallets SET balance_cents = balance_cents + 100"))


def raw_driver_write(connection: Connection) -> None:
    # ruleid: toutbet-balance-write-outside-money
    connection.exec_driver_sql("update Wallets set balance_cents = 0")


def legitimate_money_access(db: Session, wallet: Wallet, user_id: uuid.UUID) -> int:
    # ok: toutbet-balance-write-outside-money
    db.add(Wallet(user_id=user_id, balance_cents=0))
    # ok: toutbet-balance-write-outside-money
    db.execute(text("SELECT balance_cents FROM wallets"))
    # ok: toutbet-balance-write-outside-money
    transfer(db, entry_type="bonus", source="psp:clearing", target="wallet:x", amount_cents=100)
    # ok: toutbet-balance-write-outside-money
    return wallet.balance_cents


class BadStake:
    # ruleid: toutbet-money-float
    amount_cents: float
    # ruleid: toutbet-money-float
    bookie_commission_bps: Decimal = Decimal(0)
    # ruleid: toutbet-money-float
    balance_cents: Mapped[float]
    # ruleid: toutbet-money-float
    min_stake_cents: float | None = None


class GoodStake:
    # ok: toutbet-money-float
    amount_cents: int
    # ok: toutbet-money-float
    balance_cents: Mapped[int]
    # ok: toutbet-money-float
    last_head: float | None = None


def bad_fee(amount_cents: int, pool_cents: int, commission_bps: int, raw: str) -> list[object]:
    return [
        # ruleid: toutbet-money-float
        amount_cents / 2,
        # ruleid: toutbet-money-float
        pool_cents * commission_bps / 10_000,
        # ruleid: toutbet-money-float
        1 / pool_cents,
        # ruleid: toutbet-money-float
        pool_cents * 0.05,
        # ruleid: toutbet-money-float
        float(amount_cents),
        # ruleid: toutbet-money-float
        Decimal(raw) + Decimal(amount_cents),
    ]


def good_fee(
    amount_cents: int, pool_cents: int, commission_bps: int, elapsed: float
) -> list[object]:
    return [
        # ok: toutbet-money-float
        pool_cents * commission_bps // 10_000,
        # ok: toutbet-money-float
        divmod(amount_cents, 3),
        # ok: toutbet-money-float
        elapsed / 2,
    ]


def build_router() -> APIRouter:
    router = APIRouter(prefix="/api/admin")

    # ruleid: toutbet-route-without-auth
    @router.post("/credit")
    def credit(amount_cents: int) -> int:
        return amount_cents

    # ruleid: toutbet-route-without-auth
    @router.get("/users/{user_id}")
    async def read_user(user_id: uuid.UUID) -> uuid.UUID:
        return user_id

    # ok: toutbet-route-without-auth
    @router.get("/me")
    def me(user: AuthenticatedUser) -> uuid.UUID:
        return user.id

    # ok: toutbet-route-without-auth
    @router.post("/stakes")
    def stake(user: Player) -> uuid.UUID:
        return user.id

    # ok: toutbet-route-without-auth
    @router.post("/decisions")
    def decide(user: Moderator) -> uuid.UUID:
        return user.id

    # ok: toutbet-route-without-auth
    @router.post("/login")
    def login() -> None:
        return None

    return router


def build_app() -> FastAPI:
    app = FastAPI()

    # ruleid: toutbet-route-without-auth
    @app.get("/debug")
    def debug() -> None:
        return None

    return app


def build_protected() -> list[APIRouter]:
    router = protected_router(prefix="/api/bets")
    explicit = APIRouter(prefix="/api/wallet", dependencies=[Depends(current_user)])

    # ok: toutbet-route-without-auth
    @router.get("")
    def list_bets() -> None:
        return None

    # ok: toutbet-route-without-auth
    @explicit.get("")
    def read_wallet() -> None:
        return None

    return [router, explicit]
