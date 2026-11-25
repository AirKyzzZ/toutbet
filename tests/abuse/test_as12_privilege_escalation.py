import re
import uuid

import jwt
from fastapi.routing import APIRoute, iter_route_contexts
from fastapi.testclient import TestClient
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from support import Api
from toutbet.api import PUBLIC_ROUTES
from toutbet.models import User
from toutbet.security.authz import current_user
from toutbet.security.tokens import decode_access_token


def _depends_on_current_user(route: APIRoute) -> bool:
    pending = list(route.dependant.dependencies)
    while pending:
        dependency = pending.pop()
        if dependency.call is current_user:
            return True
        pending.extend(dependency.dependencies)
    return False


def test_as12_role_field_rejected_on_register(api: Api) -> None:
    for field, value in [("role", "moderator"), ("kyc_status", "verified"), ("is_admin", True)]:
        response = api.register(**{field: value})
        assert response.status_code == 422

    with api.db() as db:
        assert db.scalars(select(User)).all() == []


def test_as12_role_claim_in_token_grants_nothing(api: Api, client: TestClient) -> None:
    player = api.signup()
    secret = api.settings.jwt_secret.get_secret_value()
    claims = jwt.decode(player.token, secret, algorithms=["HS256"], audience="toutbet-api")
    escalated = jwt.encode({**claims, "role": "moderator"}, secret, algorithm="HS256")

    with_claim = client.get(
        "/api/moderation/disputes", headers={"Authorization": f"Bearer {escalated}"}
    )
    genuine = client.get("/api/moderation/disputes", headers=player.headers)

    assert with_claim.status_code == 401
    assert genuine.status_code == 403
    decoded = decode_access_token(player.token, secret, api.clock.now())
    assert decoded.user_id == uuid.UUID(player.id)


def test_as12_all_api_routes_require_auth_except_public_list(api: Api, client: TestClient) -> None:
    contexts = list(iter_route_contexts(client.app.routes))
    checked = 0

    for context in contexts:
        route = context.original_route
        if not isinstance(route, APIRoute):
            continue
        for method in sorted(context.methods or ()):
            path = context.path or ""
            if (method, path) in PUBLIC_ROUTES:
                continue
            assert path.startswith("/api/"), f"{method} {path} is outside /api"
            assert _depends_on_current_user(route), f"{method} {path} has no auth dependency"
            concrete = re.sub(r"\{[^}]+\}", str(uuid.uuid4()), path)
            response = client.request(method, concrete, json={})
            assert response.status_code == 401, f"{method} {path} answered {response.status_code}"
            checked += 1

    assert checked >= 20


def test_as12_non_moderator_gets_403_on_moderation_routes(api: Api, client: TestClient) -> None:
    player = api.signup()
    bet_id = uuid.uuid4()

    responses = [
        client.get("/api/moderation/disputes", headers=player.headers),
        client.get(f"/api/moderation/bets/{bet_id}", headers=player.headers),
        client.post(
            f"/api/moderation/bets/{bet_id}/decision",
            json={"outcome": "OUI", "justification": "Je tranche mon propre litige."},
            headers=player.headers,
        ),
    ]

    assert [r.status_code for r in responses] == [403, 403, 403]
    assert all(r.json()["error"]["code"] == "FORBIDDEN" for r in responses)


def test_as12_role_is_read_from_database_on_each_request(
    api: Api, client: TestClient, owner_session: Session
) -> None:
    player = api.signup()
    before = client.get("/api/moderation/disputes", headers=player.headers)

    with owner_session.begin():
        owner_session.execute(
            update(User).where(User.id == uuid.UUID(player.id)).values(role="moderator")
        )
    promoted = client.get("/api/moderation/disputes", headers=player.headers)
    with owner_session.begin():
        owner_session.execute(
            update(User).where(User.id == uuid.UUID(player.id)).values(role="user")
        )
    demoted = client.get("/api/moderation/disputes", headers=player.headers)

    assert [before.status_code, promoted.status_code, demoted.status_code] == [403, 200, 403]
