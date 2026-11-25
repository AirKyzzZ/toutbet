import json
from collections import defaultdict, deque
from collections.abc import Iterable, Iterator
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from typing import Any

CRITICAL = "critique"
HIGH = "haute"
LOGIN_ROUTE = "/api/auth/login"
LOGIN_FAILURE_STATUSES = (401, 429)


@dataclass(frozen=True)
class Alert:
    name: str
    severity: str
    ts: str
    key: str | None

    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False)


class SlidingCount:
    def __init__(self, threshold: int, window: timedelta) -> None:
        self.threshold = threshold
        self.window = window
        self._hits: dict[str, deque[datetime]] = defaultdict(deque)

    def add(self, key: str, moment: datetime) -> bool:
        hits = self._hits[key]
        hits.append(moment)
        while moment - hits[0] > self.window:
            hits.popleft()
        if len(hits) < self.threshold:
            return False
        hits.clear()
        return True

    def reset(self, key: str) -> None:
        self._hits.pop(key, None)


def _parse(line: str) -> tuple[dict[str, Any], datetime] | None:
    try:
        event = json.loads(line)
        moment = datetime.fromisoformat(event["ts"])
    except (ValueError, TypeError, KeyError):
        return None
    return event, moment


def evaluate(lines: Iterable[str]) -> Iterator[Alert]:
    refused_payments = SlidingCount(3, timedelta(hours=1))
    bad_webhooks = SlidingCount(3, timedelta(minutes=5))
    login_failures = SlidingCount(20, timedelta(minutes=1))
    for line in lines:
        parsed = _parse(line)
        if parsed is None:
            continue
        event, moment = parsed
        name, ts = event.get("event"), str(event["ts"])
        user_id, client_ip = str(event.get("user_id")), str(event.get("client_ip"))
        if name == "ledger.verify_failed":
            yield Alert("GRAND_LIVRE_INCOHERENT", CRITICAL, ts, None)
        elif name == "psp.webhook_rejected" and bad_webhooks.add("all", moment):
            yield Alert("WEBHOOK_PSP_SIGNATURE_INVALIDE", CRITICAL, ts, client_ip)
        elif name == "money.deposit_failed" and refused_payments.add(user_id, moment):
            yield Alert("PAIEMENTS_REFUSES_EN_SERIE", HIGH, ts, user_id)
        elif name == "money.deposit_credited":
            refused_payments.reset(user_id)
        elif (
            name == "http.request"
            and event.get("route") == LOGIN_ROUTE
            and event.get("status") in LOGIN_FAILURE_STATUSES
            and login_failures.add(client_ip, moment)
        ):
            yield Alert("FORCE_BRUTE_CONNEXION", HIGH, ts, client_ip)
