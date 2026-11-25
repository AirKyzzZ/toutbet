import uuid

from fastapi import APIRouter, Request

from toutbet.api.deps import ClockDep, SettingsDep, protected_router
from toutbet.db import DbSession
from toutbet.rate_limit import RateLimits
from toutbet.schemas.bets import InvitationAccept, InvitationAccepted, InvitationCreated
from toutbet.security.authz import AuthenticatedUser, Player
from toutbet.services.invitations import accept_invitation, create_invitation


def build_router(limits: RateLimits) -> APIRouter:
    router = protected_router(prefix="/api", tags=["invitations"])

    @router.post("/bets/{bet_id}/invitations", status_code=201, response_model=InvitationCreated)
    @limits.writes()
    def invite(
        request: Request,
        bet_id: uuid.UUID,
        user: AuthenticatedUser,
        db: DbSession,
        clock: ClockDep,
        settings: SettingsDep,
    ) -> InvitationCreated:
        with db.begin():
            url, expires_at = create_invitation(
                db,
                user=user,
                bet_id=bet_id,
                now=clock.now(),
                public_base_url=settings.public_base_url,
            )
        return InvitationCreated(invitation_url=url, expires_at=expires_at)

    @router.post("/invitations/accept", response_model=InvitationAccepted)
    @limits.per_user(limits.settings.rate_limit_invitation_accept)
    def accept(
        request: Request,
        body: InvitationAccept,
        user: Player,
        db: DbSession,
        clock: ClockDep,
    ) -> InvitationAccepted:
        with db.begin():
            bet_id = accept_invitation(db, user=user, token=body.token, now=clock.now())
        return InvitationAccepted(bet_id=bet_id)

    return router
