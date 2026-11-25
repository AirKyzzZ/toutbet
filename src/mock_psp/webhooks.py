import json
import logging
import time
from typing import Any

import httpx

from mock_psp.models import new_id
from mock_psp.settings import MockPspSettings
from toutbet.observability import log_event
from toutbet.security.webhook_signature import SIGNATURE_HEADER, sign_webhook

WEBHOOK_TIMEOUT = httpx.Timeout(5.0, connect=2.0)


def send_webhook(settings: MockPspSettings, event_type: str, data: dict[str, Any]) -> bool:
    event = {"event_id": new_id("evt"), "type": event_type, "data": data}
    body = json.dumps(event).encode("utf-8")
    secret = settings.webhook_secret.get_secret_value().encode("utf-8")
    headers = {
        "Content-Type": "application/json",
        SIGNATURE_HEADER: sign_webhook(secret, int(time.time()), body),
    }
    try:
        response = httpx.post(
            settings.webhook_url, content=body, headers=headers, timeout=WEBHOOK_TIMEOUT
        )
    except httpx.HTTPError:
        log_event("mock_psp.webhook_failed", level=logging.WARNING, event_type=event_type)
        return False
    log_event("mock_psp.webhook_sent", event_type=event_type, status=response.status_code)
    return response.status_code == 200
