import io
import json
import logging

import jwt

from toutbet.observability import build_log_handler, log_event, redact, redact_text

TOKEN = jwt.encode({"sub": "user"}, "k" * 32, algorithm="HS256")


def test_redaction_masks_card_numbers_and_jwts() -> None:
    text = f"card 4111111111111111 or 4111 1111 1111 1111 token {TOKEN} mail alice@example.org"

    masked = redact_text(text)

    assert "4111" not in masked
    assert TOKEN not in masked
    assert "eyJ" not in masked
    assert "alice@example.org" not in masked
    assert "a***@example.org" in masked


def test_redaction_keeps_hashes_and_identifiers_intact() -> None:
    hashes = [
        "1234567890123456" + "ab" * 24,
        "ab" * 24 + "1234567890123456",
        "f" + "0123456789012345678" + "e" * 44,
    ]

    for value in hashes:
        assert redact_text(f"head {value} published") == f"head {value} published"
    assert redact_text("request 4111-1111-1111-1111 done") == "request *** done"


def test_redaction_masks_sensitive_keys_recursively() -> None:
    fields = {
        "password": "hunter2-hunter2",
        "Authorization": "Bearer abc",
        "psp_api_key": "sk_live_x",
        "X-PSP-Signature": "t=1,v1=abc",
        "iban": "FR7630006000011234567890189",
        "nested": {"new_password": "x", "cvv": "123", "items": [{"card_number": "4242"}]},
        "user_id": "3f1c",
        "amount_cents": 1000,
    }

    masked = redact(fields)

    assert masked["password"] == masked["Authorization"] == masked["psp_api_key"] == "***"
    assert masked["X-PSP-Signature"] == masked["iban"] == "***"
    assert masked["nested"]["new_password"] == masked["nested"]["cvv"] == "***"
    assert masked["nested"]["items"][0]["card_number"] == "***"
    assert masked["user_id"] == "3f1c"
    assert masked["amount_cents"] == 1000


def test_redaction_filter_covers_messages_fields_and_tracebacks() -> None:
    stream = io.StringIO()
    logger = logging.getLogger("toutbet.redaction-test")
    handler = build_log_handler(stream)
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False
    try:
        logger.info("login with %s", TOKEN)
        try:
            raise ValueError("bad card 5555555555554444")
        except ValueError:
            logger.exception("failure")
        root = logging.getLogger()
        app_logger = logging.getLogger("toutbet")
        previous = app_logger.level
        root.addHandler(handler)
        app_logger.setLevel(logging.INFO)
        try:
            log_event("auth.test", token=TOKEN, email="bob@example.org", user_id="u1")
        finally:
            root.removeHandler(handler)
            app_logger.setLevel(previous)
    finally:
        logger.removeHandler(handler)

    output = stream.getvalue()
    assert TOKEN not in output
    assert "5555555555554444" not in output
    assert "bob@example.org" not in output
    lines = [json.loads(line) for line in output.splitlines()]
    assert lines[1]["exc"].startswith("Traceback")
    assert lines[2]["token"] == "***"
    assert lines[2]["email"] == "b***@example.org"
