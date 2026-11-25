import io
import json
import uuid

from fastapi.testclient import TestClient
from sqlalchemy import select

from support import Api
from toutbet.models import LedgerEntry


def _events(stream: io.StringIO) -> list[dict[str, object]]:
    return [json.loads(line) for line in stream.getvalue().splitlines()]


def test_logs_are_json_lines_with_request_id(
    api: Api, client: TestClient, captured_logs: io.StringIO
) -> None:
    player = api.signup()
    bet_id = uuid.uuid4()
    captured_logs.truncate(0)
    captured_logs.seek(0)

    response = client.get(f"/api/bets/{bet_id}", headers=player.headers)

    events = _events(captured_logs)
    access = [event for event in events if event["event"] == "http.request"]
    assert len(access) == 1
    line = access[0]
    assert line["request_id"] == response.headers["x-request-id"]
    assert line["route"] == "/api/bets/{bet_id}"
    assert str(bet_id) not in json.dumps(line)
    assert line["status"] == 404
    assert line["method"] == "GET"
    assert line["user_id"] == player.id
    assert line["client_ip"] == "testclient"
    assert isinstance(line["duration_ms"], float | int)
    assert str(line["ts"]).endswith("+00:00")
    assert line["level"] == "info"


def test_request_id_stored_in_ledger_entry(api: Api) -> None:
    player = api.signup()
    deposit_id = api.start_deposit(player, 2000).json()["deposit_id"]

    webhook = api.signed_webhook(api.payment_event(deposit_id))

    with api.db() as db:
        entry = db.scalars(select(LedgerEntry)).one()
    assert entry.request_id == webhook.headers["x-request-id"]
    assert entry.idempotency_key is not None
    assert entry.idempotency_key.startswith("evt_")
