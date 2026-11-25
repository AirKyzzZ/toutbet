import hashlib
import secrets
from collections.abc import Callable
from datetime import timedelta

from fastapi.testclient import TestClient
from sqlalchemy import select

from support import Api, FakeClock, FakePsp, Player
from toutbet.models import Invitation, Participant


def test_as06_invitation_accept_rate_limited(
    make_client: Callable[..., TestClient], clock: FakeClock, psp: FakePsp
) -> None:
    client = make_client(rate_limit_invitation_accept="10/minute")
    api = Api(client, clock, psp)
    attacker = api.signup()

    statuses = [api.join(attacker, secrets.token_urlsafe(32)).status_code for _ in range(11)]

    assert statuses[:10] == [404] * 10
    assert statuses[10] == 429


def test_as06_used_invitation_cannot_be_reused(api: Api) -> None:
    bookie = api.signup()
    friend, intruder = api.signup(), api.signup()
    bet = api.create_bet(bookie)
    token = api.invite(bookie, bet["id"])
    api.join(friend, token)

    response = api.join(intruder, token)

    assert response.status_code == 404
    with api.db() as db:
        participants = db.scalars(select(Participant.user_id)).all()
        invitation = db.scalars(select(Invitation)).one()
    assert [str(p) for p in participants] == [friend.id]
    assert str(invitation.used_by) == friend.id


def test_as06_non_participant_cannot_read_or_stake(api: Api, client: TestClient) -> None:
    bookie = api.signup()
    intruder: Player = api.signup()
    api.fund(intruder, 5000)
    bet = api.create_bet(bookie)

    read = client.get(f"/api/bets/{bet['id']}", headers=intruder.headers)
    stake = api.stake(intruder, bet["id"], "OUI", 1000)

    assert read.status_code == stake.status_code == 404
    assert bet["title"] not in read.text
    assert api.balance(intruder) == 5000


def test_as06_expired_invitation_rejected(api: Api) -> None:
    bookie = api.signup()
    player = api.signup()
    bet = api.create_bet(
        bookie,
        closes_at=(api.clock.now() + timedelta(days=3)).isoformat(),
        event_starts_at=(api.clock.now() + timedelta(days=4)).isoformat(),
    )
    token = api.invite(bookie, bet["id"])
    api.clock.advance(timedelta(hours=48))
    api.relogin(player)

    response = api.join(player, token)

    assert response.status_code == 404
    with api.db() as db:
        assert db.scalars(select(Participant)).all() == []


def test_invitation_token_stored_hashed(api: Api) -> None:
    bookie = api.signup()
    bet = api.create_bet(bookie)

    token = api.invite(bookie, bet["id"])

    with api.db() as db:
        rows = db.scalars(select(Invitation)).all()
    assert [row.token_hash for row in rows] == [hashlib.sha256(token.encode()).hexdigest()]
    assert all(token not in str(vars(row)) for row in rows)
