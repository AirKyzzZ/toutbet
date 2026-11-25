import json

from fastapi.routing import iter_route_contexts
from fastapi.testclient import TestClient
from sqlalchemy import select

from support import Api, Scenario, proposed_bet
from toutbet.models import AuditEvent, ModerationDecision


def _disputed(api: Api) -> Scenario:
    scenario = proposed_bet(
        api,
        [("alice", "OUI", 4000), ("bob", "NON", 4200)],
        outcome="NON",
        bookie_commission_bps=400,
    )
    api.contest(
        scenario.players["alice"], scenario.bet["id"], reason="Matis a réussi du premier coup."
    )
    return scenario


def test_us12_1_dispute_queue_shows_proposal_reasons_totals_and_pseudonyms(
    api: Api, client: TestClient
) -> None:
    scenario = _disputed(api)
    moderator = api.moderator()

    response = client.get("/api/moderation/disputes", headers=moderator.headers)

    items = response.json()["items"]
    assert response.status_code == 200
    assert len(items) == 1
    dispute = items[0]
    assert dispute["proposed_outcome"] == "NON"
    assert dispute["totals"] == {"OUI": 4000, "NON": 4200}
    assert dispute["bookie"] == "bookie"
    assert dispute["contests"][0]["contester"] == "alice"
    assert dispute["contests"][0]["reason"] == "Matis a réussi du premier coup."
    assert "@" not in response.text
    assert scenario.players["alice"].id not in response.text
    detail = client.get(f"/api/moderation/bets/{scenario.bet['id']}", headers=moderator.headers)
    assert detail.json()["id"] == scenario.bet["id"]


def test_us12_2_decision_settles_bet_and_is_chained_in_audit(api: Api) -> None:
    scenario = _disputed(api)
    moderator = api.moderator()
    justification = "La vidéo montre clairement un créneau réussi."

    response = api.decide(moderator, scenario.bet["id"], "OUI", justification)

    assert response.status_code == 200
    assert response.json()["status"] == "settled"
    assert api.balance(scenario.players["alice"]) == 4000 + 4200 - 210
    with api.db() as db:
        decision = db.scalars(select(ModerationDecision)).one()
        event = db.scalars(
            select(AuditEvent).where(AuditEvent.event_type == "moderation.decision")
        ).one()
    assert str(decision.moderator_id) == moderator.id
    assert str(event.actor_id) == moderator.id
    assert json.loads(event.payload)["justification"] == justification


def test_us12_3_moderators_are_created_only_by_the_admin_cli(api: Api, client: TestClient) -> None:
    paths = [context.path or "" for context in iter_route_contexts(client.app.routes)]

    with_role = api.register(role="moderator")

    assert with_role.status_code == 422
    assert not any("admin" in path or "moderators" in path for path in paths)
    assert all(not path.startswith("/api/moderation/users") for path in paths)


def test_us12_4_moderator_cannot_stake_create_or_join(api: Api, client: TestClient) -> None:
    bookie = api.signup()
    bet = api.create_bet(bookie)
    token = api.invite(bookie, bet["id"])
    moderator = api.moderator()

    join = api.join(moderator, token)
    create = client.post("/api/bets", json=api.bet_body(), headers=moderator.headers)
    stake = api.stake(moderator, bet["id"], "OUI", 1000)

    assert join.status_code == create.status_code == stake.status_code == 403
    second_player = api.signup()
    assert api.join(second_player, token).status_code == 200
