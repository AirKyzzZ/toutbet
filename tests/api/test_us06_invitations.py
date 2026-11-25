import hashlib
from datetime import datetime, timedelta

from fastapi.testclient import TestClient
from sqlalchemy import select

from support import Api
from toutbet.models import Invitation, Participant


def test_us06_1_invitation_has_256_bit_token_hashed_and_valid_48h(
    api: Api, client: TestClient
) -> None:
    bookie = api.signup()
    bet = api.create_bet(bookie)

    response = client.post(f"/api/bets/{bet['id']}/invitations", headers=bookie.headers)

    body = response.json()
    assert response.status_code == 201
    prefix = "https://toutbet.test/#/invite/"
    assert body["invitation_url"].startswith(prefix)
    token = body["invitation_url"].removeprefix(prefix)
    assert len(token) == 43
    expires_at = datetime.fromisoformat(body["expires_at"])
    assert expires_at - api.clock.now() == timedelta(hours=48)
    with api.db() as db:
        stored = db.scalars(select(Invitation)).one()
        assert stored.token_hash == hashlib.sha256(token.encode()).hexdigest()
        assert token not in stored.token_hash


def test_us06_2_accepting_adds_participant_and_consumes_token_atomically(api: Api) -> None:
    bookie = api.signup()
    player = api.signup()
    bet = api.create_bet(bookie)
    token = api.invite(bookie, bet["id"])

    response = api.join(player, token)

    assert response.status_code == 200
    assert response.json() == {"bet_id": bet["id"]}
    with api.db() as db:
        invitation = db.scalars(select(Invitation)).one()
        participant = db.scalars(select(Participant)).one()
        assert invitation.used_at is not None
        assert str(invitation.used_by) == player.id
        assert str(participant.user_id) == player.id
        assert participant.joined_at == invitation.used_at


def test_us06_3_unknown_expired_used_or_closed_invitations_get_same_404(api: Api) -> None:
    bookie = api.signup()
    first, second = api.signup(), api.signup()
    bet = api.create_bet(bookie)
    used = api.invite(bookie, bet["id"])
    api.join(first, used)
    expired = api.invite(bookie, bet["id"])
    closed_bet = api.create_bet(bookie)
    closed = api.invite(bookie, closed_bet["id"])
    api.client.post(f"/api/bets/{closed_bet['id']}/cancel", headers=bookie.headers)
    api.clock.advance(timedelta(hours=48, seconds=1))
    api.relogin(second)

    responses = [
        api.join(second, "A" * 43),
        api.join(second, used),
        api.join(second, expired),
        api.join(second, closed),
    ]

    assert {r.status_code for r in responses} == {404}
    assert len({r.json()["error"]["message"] for r in responses}) == 1


def test_us06_4_fifty_active_invitations_maximum(api: Api, client: TestClient) -> None:
    bookie = api.signup()
    bet = api.create_bet(bookie)
    path = f"/api/bets/{bet['id']}/invitations"

    statuses = [client.post(path, headers=bookie.headers).status_code for _ in range(51)]

    assert statuses[:50] == [201] * 50
    assert statuses[50] == 409
    assert client.post(path, headers=bookie.headers).json()["error"]["code"] == "INVITATION_LIMIT"


def test_us06_5_bets_readable_only_by_bookie_and_participants(api: Api, client: TestClient) -> None:
    bookie = api.signup()
    player = api.signup()
    outsider = api.signup()
    bet = api.create_bet(bookie)
    api.add_participants(bookie, bet["id"], player)

    assert client.get(f"/api/bets/{bet['id']}", headers=bookie.headers).status_code == 200
    assert client.get(f"/api/bets/{bet['id']}", headers=player.headers).status_code == 200
    assert client.get(f"/api/bets/{bet['id']}", headers=outsider.headers).status_code == 404
    assert client.get("/api/bets", headers=outsider.headers).json()["items"] == []
    listed = client.get("/api/bets", headers=player.headers).json()["items"]
    assert [item["id"] for item in listed] == [bet["id"]]
