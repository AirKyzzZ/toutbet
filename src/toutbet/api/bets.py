import uuid
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query, Request

from toutbet.api.deps import ClockDep, protected_router, require_feature, trace_for
from toutbet.db import DbSession
from toutbet.rate_limit import RateLimits
from toutbet.schemas.bets import BetCreate, BetPage, BetView, ContestCreate, ResultProposal
from toutbet.schemas.common import MAX_PAGE_SIZE
from toutbet.security.authz import AuthenticatedUser, Player
from toutbet.services.bet_views import build_bet_view
from toutbet.services.bets import cancel_bet, create_bet, find_bet_for, list_bets
from toutbet.services.results import contest_result, propose_result

StatusFilter = Literal["open", "closed", "result_proposed", "disputed", "settled", "cancelled"]
RoleFilter = Literal["bookie", "participant"]


def build_router(limits: RateLimits) -> APIRouter:
    router = protected_router(prefix="/api/bets", tags=["bets"])

    @router.post(
        "",
        status_code=201,
        response_model=BetView,
        dependencies=[Depends(require_feature("bet_creation_enabled"))],
    )
    @limits.writes()
    def create(
        request: Request, body: BetCreate, user: Player, db: DbSession, clock: ClockDep
    ) -> BetView:
        with db.begin():
            bet = create_bet(db, user=user, data=body, now=clock.now())
            return build_bet_view(db, bet, user.id)

    @router.get("", response_model=BetPage)
    @limits.reads()
    def list_mine(
        request: Request,
        user: AuthenticatedUser,
        db: DbSession,
        status: StatusFilter | None = None,
        role: RoleFilter | None = None,
        page: Annotated[int, Query(ge=1, le=10_000)] = 1,
        page_size: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = MAX_PAGE_SIZE,
    ) -> BetPage:
        with db.begin():
            bets = list_bets(
                db, user=user, status=status, role=role, page=page, page_size=page_size
            )
            items = [build_bet_view(db, bet, user.id) for bet in bets]
        return BetPage(page=page, page_size=page_size, items=items)

    @router.get("/{bet_id}", response_model=BetView)
    @limits.reads()
    def read(
        request: Request, bet_id: uuid.UUID, user: AuthenticatedUser, db: DbSession
    ) -> BetView:
        with db.begin():
            return build_bet_view(db, find_bet_for(db, user, bet_id), user.id)

    @router.post("/{bet_id}/cancel", response_model=BetView)
    @limits.writes()
    def cancel(
        request: Request,
        bet_id: uuid.UUID,
        user: AuthenticatedUser,
        db: DbSession,
        clock: ClockDep,
    ) -> BetView:
        with db.begin():
            bet = cancel_bet(
                db, user=user, bet_id=bet_id, now=clock.now(), trace=trace_for(request, user)
            )
            db.refresh(bet)
            return build_bet_view(db, bet, user.id)

    @router.post("/{bet_id}/result", response_model=BetView)
    @limits.writes()
    def propose(
        request: Request,
        bet_id: uuid.UUID,
        body: ResultProposal,
        user: AuthenticatedUser,
        db: DbSession,
        clock: ClockDep,
    ) -> BetView:
        with db.begin():
            bet = propose_result(
                db, user=user, bet_id=bet_id, outcome=body.outcome, note=body.note, now=clock.now()
            )
            db.refresh(bet)
            return build_bet_view(db, bet, user.id)

    @router.post("/{bet_id}/contests", status_code=201, response_model=BetView)
    @limits.writes()
    def contest(
        request: Request,
        bet_id: uuid.UUID,
        body: ContestCreate,
        user: AuthenticatedUser,
        db: DbSession,
        clock: ClockDep,
    ) -> BetView:
        with db.begin():
            bet = contest_result(db, user=user, bet_id=bet_id, reason=body.reason, now=clock.now())
            db.refresh(bet)
            return build_bet_view(db, bet, user.id)

    return router
