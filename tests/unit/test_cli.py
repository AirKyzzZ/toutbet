from datetime import UTC, date, datetime
from pathlib import Path

import pytest
from sqlalchemy import inspect, select, update
from sqlalchemy.orm import Session

from toutbet import cli
from toutbet.config import load_settings
from toutbet.db import make_engine
from toutbet.models import User, Wallet
from toutbet.security.passwords import verify_password

MODERATOR_PASSWORD = "moderator password from stdin"


@pytest.fixture(autouse=True)
def cli_environment(database_url: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DATABASE_URL", database_url)
    monkeypatch.delenv("DATABASE_ADMIN_URL", raising=False)
    monkeypatch.setattr(cli, "configure_logging", lambda: None)


def test_create_moderator_reads_password_from_stdin(
    database_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    prompts: list[str] = []

    def fake_getpass(prompt: str = "") -> str:
        prompts.append(prompt)
        return MODERATOR_PASSWORD

    monkeypatch.setattr(cli.getpass, "getpass", fake_getpass)

    code = cli.main(
        [
            "create-moderator",
            "--email",
            "Mod@ToutBet.test",
            "--display-name",
            "mod_1",
            "--birth-date",
            "1985-03-02",
        ]
    )

    assert code == 0
    assert len(prompts) == 2
    engine = make_engine(database_url)
    with Session(engine) as db:
        user = db.execute(select(User).where(User.email == "mod@toutbet.test")).scalar_one()
        assert user.role == "moderator"
        assert verify_password(user.password_hash, MODERATOR_PASSWORD)
        assert db.get(Wallet, user.id) is not None
    engine.dispose()
    with pytest.raises(SystemExit):
        cli.main(
            [
                "create-moderator",
                "--email",
                "x@toutbet.test",
                "--display-name",
                "mod_2",
                "--birth-date",
                "1985-03-02",
                "--password",
                MODERATOR_PASSWORD,
            ]
        )


def test_create_moderator_rejects_mismatched_or_weak_password(
    database_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    answers = iter(["first password value", "second password value", "short", "short"])
    monkeypatch.setattr(cli.getpass, "getpass", lambda prompt="": next(answers))
    arguments = [
        "create-moderator",
        "--email",
        "m@toutbet.test",
        "--display-name",
        "mod_x",
        "--birth-date",
        "1985-03-02",
    ]

    assert cli.main(arguments) == 1
    assert cli.main(arguments) == 1
    engine = make_engine(database_url)
    with Session(engine) as db:
        assert db.scalars(select(User)).all() == []
    engine.dispose()


def test_gen_secrets_prints_fresh_valid_values(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["gen-secrets"]) == 0
    first = capsys.readouterr().out
    assert cli.main(["gen-secrets"]) == 0
    second = capsys.readouterr().out

    values = dict(line.split("=", 1) for line in first.strip().splitlines())
    assert set(values) == {"JWT_SECRET", "RECEIPT_SIGNING_KEY", "PSP_WEBHOOK_SECRET", "PSP_API_KEY"}
    assert first != second
    settings = load_settings(
        _env_file=None,
        jwt_secret=values["JWT_SECRET"],
        receipt_signing_key=values["RECEIPT_SIGNING_KEY"],
        psp_webhook_secret=values["PSP_WEBHOOK_SECRET"],
        psp_api_key=values["PSP_API_KEY"],
        psp_base_url="https://psp.test",
        public_base_url="https://toutbet.test",
    )
    assert settings.jwt_secret.get_secret_value() == values["JWT_SECRET"]


def test_verify_ledger_command_fails_on_tampering(
    database_url: str, capsys: pytest.CaptureFixture[str]
) -> None:
    assert cli.main(["verify-ledger"]) == 0
    engine = make_engine(database_url)
    with Session(engine) as db, db.begin():
        user = User(
            email="t@example.org",
            password_hash="x",
            display_name="tamper",
            birth_date=date(1990, 1, 1),
            created_at=datetime(2026, 11, 25, tzinfo=UTC),
        )
        db.add(user)
        db.flush()
        db.add(Wallet(user_id=user.id, balance_cents=0))
        db.flush()
        db.execute(update(Wallet).values(balance_cents=100))
    engine.dispose()

    assert cli.main(["verify-ledger"]) == 1
    assert "differs from ledger" in capsys.readouterr().err


def test_init_db_creates_schema_and_chain_heads(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    url = f"sqlite:///{tmp_path / 'fresh.db'}"
    monkeypatch.setenv("DATABASE_URL", url)

    assert cli.main(["init-db"]) == 0

    engine = make_engine(url)
    tables = set(inspect(engine).get_table_names())
    engine.dispose()
    assert {"users", "wallets", "ledger_entries", "audit_events", "chain_heads"} <= tables
    assert cli.main(["verify-ledger"]) == 0
