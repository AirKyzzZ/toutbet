import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request

from toutbet.api.deps import ClockDep, protected_router, trace_for
from toutbet.db import DbSession
from toutbet.rate_limit import RateLimits
from toutbet.schemas.common import MAX_PAGE_SIZE
from toutbet.schemas.moderation import DecisionCreate, DecisionResult, DisputePage, DisputeView
from toutbet.security.authz import Moderator, require_moderator
from toutbet.services.bet_views import build_dispute_view
from toutbet.services.moderation import decide, get_dispute, list_disputes


def build_router(limits: RateLimits) -> APIRouter:
    router = protected_router(
        prefix="/api/moderation",
        tags=["moderation"],
        dependencies=[Depends(require_moderator)],
    )

    @router.get("/disputes", response_model=DisputePage)
    @limits.reads()
    def disputes(
        request: Request,
        db: DbSession,
        page: Annotated[int, Query(ge=1, le=10_000)] = 1,
        page_size: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = MAX_PAGE_SIZE,
    ) -> DisputePage:
        with db.begin():
            bets = list_disputes(db, page=page, page_size=page_size)
            items = [build_dispute_view(db, bet) for bet in bets]
        return DisputePage(page=page, page_size=page_size, items=items)

    @router.get("/bets/{bet_id}", response_model=DisputeView)
    @limits.reads()
    def dispute(request: Request, bet_id: uuid.UUID, db: DbSession) -> DisputeView:
        with db.begin():
            return build_dispute_view(db, get_dispute(db, bet_id))

    @router.post("/bets/{bet_id}/decision", response_model=DecisionResult)
    @limits.writes()
    def decision(
        request: Request,
        bet_id: uuid.UUID,
        body: DecisionCreate,
        moderator: Moderator,
        db: DbSession,
        clock: ClockDep,
    ) -> DecisionResult:
        with db.begin():
            bet = decide(
                db,
                moderator=moderator,
                bet_id=bet_id,
                outcome=body.outcome,
                justification=body.justification,
                now=clock.now(),
                trace=trace_for(request, moderator),
            )
            db.refresh(bet)
            return DecisionResult(bet_id=bet.id, outcome=body.outcome, status=bet.status)

    return router
