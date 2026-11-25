from typing import Annotated

from fastapi import APIRouter, Query, Request
from sqlalchemy import or_, select

from toutbet.api.deps import protected_router
from toutbet.db import DbSession
from toutbet.models import LedgerEntry
from toutbet.rate_limit import RateLimits
from toutbet.schemas.common import MAX_PAGE_SIZE
from toutbet.schemas.wallet import LedgerLine, WalletResponse
from toutbet.security.authz import AuthenticatedUser
from toutbet.services.money import balance_of, wallet_account


def build_router(limits: RateLimits) -> APIRouter:
    router = protected_router(prefix="/api", tags=["wallet"])

    @router.get("/wallet", response_model=WalletResponse)
    @limits.reads()
    def read_wallet(
        request: Request,
        user: AuthenticatedUser,
        db: DbSession,
        page: Annotated[int, Query(ge=1, le=10_000)] = 1,
        page_size: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = MAX_PAGE_SIZE,
    ) -> WalletResponse:
        account = wallet_account(user.id)
        with db.begin():
            balance = balance_of(db, user.id)
            entries = db.scalars(
                select(LedgerEntry)
                .where(or_(LedgerEntry.from_account == account, LedgerEntry.to_account == account))
                .order_by(LedgerEntry.seq.desc())
                .offset((page - 1) * page_size)
                .limit(page_size)
            ).all()
        return WalletResponse(
            balance_cents=balance,
            page=page,
            page_size=page_size,
            entries=[
                LedgerLine(
                    created_at=entry.created_at,
                    entry_type=entry.entry_type,
                    direction="credit" if entry.to_account == account else "debit",
                    amount_cents=entry.amount_cents,
                    bet_id=entry.bet_id,
                )
                for entry in entries
            ],
        )

    return router
