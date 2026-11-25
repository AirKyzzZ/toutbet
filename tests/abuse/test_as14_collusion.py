import json

from fastapi.testclient import TestClient
from sqlalchemy import select

from support import Api, proposed_bet, staked_bet
from toutbet.models import AuditEvent, Participant


def test_as14_bookie_cannot_stake_or_join_own_bet(api: Api) -> None:
    scenario = staked_bet(api, [])
    api.fund(scenario.bookie, 5000)
    token = api.invite(scenario.bookie, scenario.bet["id"])

    join = api.join(scenario.bookie, token)
    stake = api.stake(scenario.bookie, scenario.bet["id"], "OUI", 1000)

    assert join.status_code == stake.status_code == 403
    assert join.json()["error"]["code"] == stake.json()["error"]["code"] == "BOOKIE_CANNOT_STAKE"
    assert api.balance(scenario.bookie) == 5000
    with api.db() as db:
        assert db.scalars(select(Participant)).all() == []


def test_as14_moderator_cannot_stake_create_or_join(api: Api, client: TestClient) -> None:
    scenario = staked_bet(api, [("bob", "OUI", 1000)])
    moderator = api.moderator()
    token = api.invite(scenario.bookie, scenario.bet["id"])

    responses = [
        api.join(moderator, token),
        api.stake(moderator, scenario.bet["id"], "NON", 1000),
        client.post("/api/bets", json=api.bet_body(), headers=moderator.headers),
        client.post(
            "/api/deposits",
            json={"amount_cents": 1000},
            headers={
                **moderator.headers,
                "Idempotency-Key": "8a7f1f0e-2c1b-4b8f-9d3e-1a2b3c4d5e6f",
            },
        ),
    ]

    assert [r.status_code for r in responses] == [403, 403, 403, 403]


def test_as14_decision_requires_justification(api: Api) -> None:
    scenario = proposed_bet(api, [("alice", "OUI", 4000), ("bob", "NON", 4000)], outcome="NON")
    api.contest(scenario.players["alice"], scenario.bet["id"])
    moderator = api.moderator()

    missing = api.client.post(
        f"/api/moderation/bets/{scenario.bet['id']}/decision",
        json={"outcome": "OUI"},
        headers=moderator.headers,
    )
    short = api.decide(moderator, scenario.bet["id"], "OUI", justification="trop court")

    assert missing.status_code == short.status_code == 422
    assert api.bet(scenario.bookie, scenario.bet["id"])["status"] == "disputed"


def test_as14_whitespace_padded_justification_rejected(api: Api) -> None:
    scenario = proposed_bet(api, [("alice", "OUI", 4000), ("bob", "NON", 4000)], outcome="NON")
    api.contest(scenario.players["alice"], scenario.bet["id"])
    moderator = api.moderator()

    blank = api.decide(moderator, scenario.bet["id"], "OUI", justification=" " * 25)
    padded = api.decide(moderator, scenario.bet["id"], "OUI", justification="  ok\t\n" + " " * 20)
    short = api.decide(moderator, scenario.bet["id"], "OUI", justification="  vu  la  vidéo  ok  ")

    assert blank.status_code == padded.status_code == short.status_code == 422
    assert api.bet(scenario.bookie, scenario.bet["id"])["status"] == "disputed"
    with api.db() as db:
        decisions = db.scalars(
            select(AuditEvent).where(AuditEvent.event_type == "moderation.decision")
        ).all()
    assert decisions == []


def test_as14_decision_recorded_and_visible_to_participants(api: Api) -> None:
    scenario = proposed_bet(api, [("alice", "OUI", 4000), ("bob", "NON", 4000)], outcome="NON")
    api.contest(scenario.players["alice"], scenario.bet["id"])
    moderator = api.moderator()
    justification = "Témoins concordants, le créneau est réussi au premier essai."

    api.decide(moderator, scenario.bet["id"], "OUI", justification)

    seen = api.bet(scenario.players["bob"], scenario.bet["id"])
    assert seen["decision"]["moderator"] == moderator.display_name
    assert seen["decision"]["outcome"] == "OUI"
    assert seen["decision"]["justification"] == justification
    with api.db() as db:
        event = db.scalars(
            select(AuditEvent).where(AuditEvent.event_type == "moderation.decision")
        ).one()
        previous = db.scalars(select(AuditEvent).where(AuditEvent.seq == event.seq - 1)).one()
    payload = json.loads(event.payload)
    assert str(event.actor_id) == moderator.id
    assert payload["outcome"] == "OUI"
    assert payload["justification"] == justification
    assert event.prev_hash == previous.hash
