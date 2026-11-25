import json
from collections.abc import Callable
from datetime import datetime, timedelta

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from support import Api, FakeClock, FakePsp, proposed_bet, staked_bet
from toutbet.models import AuditEvent
from toutbet.psp.client import HttpPspClient, get_psp_client

WORKED_EXAMPLE = [
    ("alice", "OUI", 1000),
    ("bob", "OUI", 3000),
    ("chloe", "NON", 2000),
    ("david", "NON", 1500),
    ("emma", "NON", 700),
]


def test_as09_result_cannot_be_proposed_twice(api: Api) -> None:
    scenario = proposed_bet(api, [("bob", "OUI", 1000)], outcome="NON")

    again = api.propose(scenario.bookie, scenario.bet["id"], "OUI")

    assert again.status_code == 409
    assert api.bet(scenario.bookie, scenario.bet["id"])["proposed_outcome"] == "NON"


def test_as09_overturned_result_forfeits_bookie_fee(api: Api) -> None:
    scenario = proposed_bet(api, WORKED_EXAMPLE, outcome="NON", bookie_commission_bps=400)
    contest = api.contest(scenario.players["alice"], scenario.bet["id"])
    assert contest.json()["status"] == "disputed"
    moderator = api.moderator()

    decision = api.decide(moderator, scenario.bet["id"], "OUI")

    assert decision.status_code == 200
    assert api.balance(scenario.players["alice"]) == 1998
    assert api.balance(scenario.players["bob"]) == 5992
    assert api.balance(scenario.bookie) == 0
    assert api.bet(scenario.bookie, scenario.bet["id"])["bookie_fee_forfeited"] is True


def test_as09_result_proposal_recorded_in_audit_chain(api: Api) -> None:
    scenario = proposed_bet(api, [("bob", "OUI", 1000)], outcome="OUI")

    with api.db() as db:
        event = db.scalars(
            select(AuditEvent).where(AuditEvent.event_type == "bet.result_proposed")
        ).one()
        previous = db.scalars(select(AuditEvent).where(AuditEvent.seq == event.seq - 1)).one()

    assert str(event.actor_id) == scenario.bookie.id
    assert str(event.bet_id) == scenario.bet["id"]
    assert json.loads(event.payload)["outcome"] == "OUI"
    assert event.prev_hash == previous.hash
    assert event.created_at == api.clock.now()


def test_as09_participant_cannot_propose_result(api: Api) -> None:
    scenario = staked_bet(api, [("bob", "OUI", 1000)])
    api.start_event(scenario.bet, *scenario.everyone)

    response = api.propose(scenario.players["bob"], scenario.bet["id"], "OUI")

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "FORBIDDEN"
    assert api.bet(scenario.bookie, scenario.bet["id"])["proposed_outcome"] is None


def test_as09_evidence_url_never_fetched(
    make_client: Callable[..., TestClient],
    clock: FakeClock,
    psp: FakePsp,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    spied: list[str] = []

    def spy(request: httpx.Request) -> httpx.Response:
        spied.append(str(request.url))
        return httpx.Response(500)

    def any_transport(self: httpx.HTTPTransport, request: httpx.Request) -> httpx.Response:
        spied.append(str(request.url))
        return httpx.Response(500)

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", any_transport)
    client = make_client()
    api = Api(client, clock, psp)
    scenario = staked_bet(api, [("bob", "OUI", 1000)])
    spy_client = HttpPspClient("https://psp.test", "k" * 40, transport=httpx.MockTransport(spy))
    client.app.dependency_overrides[get_psp_client] = lambda: spy_client
    api.start_event(scenario.bet, *scenario.everyone)
    evidence = "Voir http://127.0.0.1:2375/containers/json et file:///etc/passwd"

    response = api.propose(scenario.bookie, scenario.bet["id"], "OUI", note=evidence)
    api.contest(scenario.players["bob"], scenario.bet["id"], reason="Regardez " + evidence[:400])

    assert response.status_code == 200
    assert spied == []


def test_as09_bet_without_result_escalates_after_72h(api: Api) -> None:
    scenario = staked_bet(api, [("bob", "OUI", 1000), ("carol", "NON", 1000)])
    starts = datetime.fromisoformat(scenario.bet["event_starts_at"])
    api.start_event(scenario.bet, *scenario.everyone)

    api.clock.set(starts + timedelta(hours=72))
    report = api.tick()
    api.relogin(*scenario.everyone)

    assert report.escalated == 1
    assert api.bet(scenario.bookie, scenario.bet["id"])["status"] == "disputed"
    moderator = api.moderator()
    disputes = api.client.get("/api/moderation/disputes", headers=moderator.headers).json()
    assert [item["id"] for item in disputes["items"]] == [scenario.bet["id"]]
    assert api.decide(moderator, scenario.bet["id"], "ANNULE").status_code == 200
    assert api.balance(scenario.players["bob"]) == 1000
