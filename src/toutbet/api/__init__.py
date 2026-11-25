from fastapi import APIRouter

from toutbet.api import (
    auth,
    bets,
    deposits,
    health,
    invitations,
    kyc,
    moderation,
    receipts,
    stakes,
    wallet,
    webhooks,
    withdrawals,
)
from toutbet.rate_limit import RateLimits

PUBLIC_ROUTES = frozenset(
    {
        ("POST", "/api/auth/register"),
        ("POST", "/api/auth/login"),
        ("POST", "/api/webhooks/psp"),
        ("GET", "/api/receipts/public-key"),
        ("GET", "/healthz"),
    }
)


def build_routers(limits: RateLimits) -> list[APIRouter]:
    return [
        health.build_router(limits),
        auth.build_public_router(limits),
        auth.build_router(limits),
        wallet.build_router(limits),
        deposits.build_router(limits),
        kyc.build_router(limits),
        withdrawals.build_router(limits),
        bets.build_router(limits),
        invitations.build_router(limits),
        stakes.build_router(limits),
        moderation.build_router(limits),
        webhooks.build_router(limits),
        receipts.build_router(limits),
    ]
