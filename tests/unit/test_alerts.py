import io
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from support import Api
from toutbet import cli
from toutbet.alerts import evaluate

START = datetime(2026, 11, 25, 20, 0, tzinfo=UTC)


def _line(seconds: float, event: str, **fields: Any) -> str:
    ts = (START + timedelta(seconds=seconds)).isoformat(timespec="milliseconds")
    return json.dumps({"ts": ts, "level": "info", "event": event, **fields})


def _names(lines: list[str]) -> list[tuple[str, str, str | None]]:
    return [(a.name, a.severity, a.key) for a in evaluate(lines)]


def test_three_refused_payments_in_a_row_alert_once_per_account() -> None:
    lines = [
        _line(0, "money.deposit_failed", user_id="alice"),
        _line(60, "money.deposit_failed", user_id="bob"),
        _line(120, "money.deposit_failed", user_id="alice"),
        _line(180, "money.deposit_credited", user_id="bob"),
        _line(240, "money.deposit_failed", user_id="bob"),
        _line(300, "money.deposit_failed", user_id="alice"),
        _line(360, "money.deposit_failed", user_id="bob"),
        _line(7200, "money.deposit_failed", user_id="carol"),
        _line(7200 + 3700, "money.deposit_failed", user_id="carol"),
        _line(7200 + 7400, "money.deposit_failed", user_id="carol"),
    ]

    assert _names(lines) == [("PAIEMENTS_REFUSES_EN_SERIE", "haute", "alice")]


def test_invalid_webhook_signatures_and_ledger_failure_are_critical() -> None:
    spread = [_line(i * 200, "psp.webhook_rejected", client_ip="203.0.113.9") for i in range(3)]
    burst = [_line(1000 + i, "psp.webhook_rejected", client_ip="203.0.113.9") for i in range(3)]
    ledger = [_line(2000, "ledger.verify_failed", detail="wallet mismatch")]

    assert _names(spread + burst + ledger) == [
        ("WEBHOOK_PSP_SIGNATURE_INVALIDE", "critique", "203.0.113.9"),
        ("GRAND_LIVRE_INCOHERENT", "critique", None),
    ]


def test_login_failures_from_one_address_within_a_minute_alert() -> None:
    def failure(seconds: float, ip: str, status: int = 401) -> str:
        return _line(seconds, "http.request", route="/api/auth/login", status=status, client_ip=ip)

    spread_out = [failure(i * 4, "198.51.100.1") for i in range(19)]
    other_ips = [failure(i, f"198.51.100.{i + 10}") for i in range(25)]
    attack = [failure(200 + i, "198.51.100.2", 401 if i < 5 else 429) for i in range(20)]
    noise = ["not json", json.dumps([1, 2]), json.dumps({"event": "http.request"})]

    assert _names(spread_out + other_ips + noise + attack) == [
        ("FORCE_BRUTE_CONNEXION", "haute", "198.51.100.2")
    ]


def test_real_refused_payments_and_forged_webhooks_raise_alerts(
    api: Api, captured_logs: io.StringIO
) -> None:
    player = api.signup()
    for _ in range(3):
        deposit_id = api.start_deposit(player, 2000).json()["deposit_id"]
        refused = {**api.payment_event(deposit_id), "type": "payment.failed"}
        assert api.signed_webhook(refused).status_code == 200
    for _ in range(3):
        forged = api.signed_webhook(api.payment_event(deposit_id), secret="f" * 48)
        assert forged.status_code == 400

    alerts = _names(captured_logs.getvalue().splitlines())

    assert alerts == [
        ("PAIEMENTS_REFUSES_EN_SERIE", "haute", player.id),
        ("WEBHOOK_PSP_SIGNATURE_INVALIDE", "critique", "testclient"),
    ]


def test_check_alerts_prints_alerts_and_fails(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cli, "configure_logging", lambda: None)
    clean = tmp_path / "clean.log"
    clean.write_text(_line(0, "worker.tick") + "\n", encoding="utf-8")
    broken = tmp_path / "broken.log"
    broken.write_text(_line(0, "ledger.verify_failed") + "\n", encoding="utf-8")

    assert cli.main(["check-alerts", str(clean)]) == 0
    assert capsys.readouterr().out == ""
    assert cli.main(["check-alerts", str(broken)]) == 1
    printed = json.loads(capsys.readouterr().out)
    assert printed["name"] == "GRAND_LIVRE_INCOHERENT"
    assert printed["severity"] == "critique"
