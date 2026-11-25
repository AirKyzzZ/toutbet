import json
from datetime import datetime, timedelta

import httpx
import pytest
from sqlalchemy import select

from support import Api, staked_bet
from toutbet.models import AuditEvent


def test_us09_1_bookie_proposes_result_and_opens_24h_contest_window(api: Api) -> None:
    scenario = staked_bet(api, [("bob", "OUI", 1000)])
    api.start_event(scenario.bet, *scenario.everyone)

    response = api.propose(scenario.bookie, scenario.bet["id"], "OUI", note="Vidéo du créneau")

    body = response.json()
    assert response.status_code == 200
    assert body["status"] == "result_proposed"
    assert body["proposed_outcome"] == "OUI"
    assert body["proposal_note"] == "Vidéo du créneau"
    deadline = datetime.fromisoformat(body["contest_deadline"])
    assert deadline - api.clock.now() == timedelta(hours=24)


def test_us09_2_proposal_recorded_once_in_audit_chain(api: Api) -> None:
    scenario = staked_bet(api, [("bob", "OUI", 1000)])
    api.start_event(scenario.bet, *scenario.everyone)
    api.propose(scenario.bookie, scenario.bet["id"], "OUI")

    second = api.propose(scenario.bookie, scenario.bet["id"], "NON")

    assert second.status_code == 409
    with api.db() as db:
        events = db.scalars(
            select(AuditEvent).where(AuditEvent.event_type == "bet.result_proposed")
        ).all()
    assert len(events) == 1
    assert json.loads(events[0].payload)["outcome"] == "OUI"
    assert api.bet(scenario.bookie, scenario.bet["id"])["proposed_outcome"] == "OUI"


def test_us09_3_other_participant_gets_403_and_outsider_404(api: Api) -> None:
    scenario = staked_bet(api, [("bob", "OUI", 1000)])
    outsider = api.signup()
    api.start_event(scenario.bet, *scenario.everyone)
    api.relogin(outsider)

    participant = api.propose(scenario.players["bob"], scenario.bet["id"], "OUI")
    stranger = api.propose(outsider, scenario.bet["id"], "OUI")

    assert participant.status_code == 403
    assert stranger.status_code == 404
    assert api.bet(scenario.bookie, scenario.bet["id"])["status"] == "closed"


def test_us09_4_proposal_requires_closed_bet_after_event_start(api: Api) -> None:
    scenario = staked_bet(api, [("bob", "OUI", 1000)])

    while_open = api.propose(scenario.bookie, scenario.bet["id"], "OUI")
    api.clock.set(datetime.fromisoformat(scenario.bet["closes_at"]))
    api.tick()
    api.relogin(*scenario.everyone)
    before_event = api.propose(scenario.bookie, scenario.bet["id"], "OUI")

    assert while_open.status_code == before_event.status_code == 409
    assert while_open.json()["error"]["code"] == "INVALID_STATE"
    assert api.bet(scenario.bookie, scenario.bet["id"])["status"] == "closed"


def test_us09_5_note_is_plain_text_never_fetched(api: Api, monkeypatch: pytest.MonkeyPatch) -> None:
    outbound: list[str] = []

    def record(self: httpx.HTTPTransport, request: httpx.Request) -> httpx.Response:
        outbound.append(str(request.url))
        return httpx.Response(200)

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", record)
    scenario = staked_bet(api, [("bob", "OUI", 1000)])
    api.start_event(scenario.bet, *scenario.everyone)
    note = "Preuve : http://169.254.169.254/latest/meta-data/ <script>x</script>"

    response = api.propose(scenario.bookie, scenario.bet["id"], "NON", note=note)

    assert response.json()["proposal_note"] == note
    assert outbound == []
