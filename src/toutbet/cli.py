import argparse
import getpass
import logging
import secrets
import sys
from datetime import date
from pathlib import Path

from pydantic import ValidationError
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from toutbet.alerts import evaluate
from toutbet.clock import SystemClock
from toutbet.config import ConfigurationError, load_database_settings
from toutbet.db import make_engine
from toutbet.models import User, create_schema
from toutbet.models.user import ROLE_MODERATOR
from toutbet.observability import configure_logging, log_event
from toutbet.schemas.auth import RegisterRequest
from toutbet.security.passwords import hash_password
from toutbet.security.receipts import b64url
from toutbet.services.accounts import create_user, is_adult
from toutbet.services.verification import verify_ledger


def _write(line: str) -> None:
    sys.stdout.write(line + "\n")


def _fail(message: str) -> int:
    sys.stderr.write(message + "\n")
    return 1


def gen_secrets(_args: argparse.Namespace) -> int:
    _write(f"JWT_SECRET={secrets.token_urlsafe(48)}")
    _write(f"RECEIPT_SIGNING_KEY={b64url(secrets.token_bytes(32))}")
    _write(f"PSP_WEBHOOK_SECRET={secrets.token_urlsafe(48)}")
    _write(f"PSP_API_KEY={secrets.token_urlsafe(48)}")
    return 0


def init_db(args: argparse.Namespace) -> int:
    settings = load_database_settings()
    admin_url = settings.database_admin_url or settings.database_url
    engine = make_engine(admin_url.get_secret_value())
    create_schema(engine)
    if args.grants is not None:
        if engine.dialect.name != "postgresql":
            return _fail("--grants is only supported on PostgreSQL")
        with engine.begin() as connection:
            connection.exec_driver_sql(Path(args.grants).read_text(encoding="utf-8"))
    engine.dispose()
    _write("database schema ready")
    return 0


def _read_new_password() -> str | None:
    password = getpass.getpass("Moderator password: ")
    confirmation = getpass.getpass("Repeat password: ")
    return password if password == confirmation else None


def create_moderator(args: argparse.Namespace) -> int:
    password = _read_new_password()
    if password is None:
        return _fail("passwords do not match")
    try:
        data = RegisterRequest(
            email=args.email,
            display_name=args.display_name,
            password=password,
            birth_date=date.fromisoformat(args.birth_date),
        )
    except (ValidationError, ValueError) as error:
        fields = []
        if isinstance(error, ValidationError):
            fields = [".".join(str(p) for p in item["loc"]) for item in error.errors()]
        return _fail(f"invalid moderator data: {', '.join(fields) or 'birth date'}")
    now = SystemClock().now()
    if not is_adult(data.birth_date, now.date()):
        return _fail("moderators must be adults")
    engine = make_engine(load_database_settings().database_url.get_secret_value())
    with Session(engine) as db, db.begin():
        taken = db.scalar(
            select(User.id).where(
                or_(
                    User.email == data.email,
                    func.lower(User.display_name) == data.display_name.lower(),
                )
            )
        )
        if taken is not None:
            return _fail("email or display name already used")
        user = create_user(
            db,
            email=data.email,
            display_name=data.display_name,
            password_hash=hash_password(data.password),
            birth_date=data.birth_date,
            role=ROLE_MODERATOR,
            now=now,
        )
        log_event("admin.moderator_created", user_id=str(user.id))
    engine.dispose()
    _write("moderator created")
    return 0


def verify(_args: argparse.Namespace) -> int:
    engine = make_engine(load_database_settings().database_url.get_secret_value())
    with Session(engine) as db, db.begin():
        report = verify_ledger(db)
    engine.dispose()
    if not report.ok:
        log_event("ledger.verify_failed", level=logging.CRITICAL, detail=report.message)
        return _fail(report.message)
    _write(report.message)
    return 0


def check_alerts(args: argparse.Namespace) -> int:
    with args.log.open(encoding="utf-8", errors="replace") as lines:
        alerts = [alert.to_json() for alert in evaluate(lines)]
    for alert in alerts:
        _write(alert)
    return 1 if alerts else 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="toutbet-admin")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("gen-secrets").set_defaults(handler=gen_secrets)
    init = commands.add_parser("init-db")
    init.add_argument("--grants", type=Path, default=None)
    init.set_defaults(handler=init_db)
    moderator = commands.add_parser("create-moderator")
    moderator.add_argument("--email", required=True)
    moderator.add_argument("--display-name", required=True)
    moderator.add_argument("--birth-date", required=True)
    moderator.set_defaults(handler=create_moderator)
    commands.add_parser("verify-ledger").set_defaults(handler=verify)
    alerts = commands.add_parser("check-alerts")
    alerts.add_argument("log", type=Path)
    alerts.set_defaults(handler=check_alerts)
    return parser


def main(argv: list[str] | None = None) -> int:
    configure_logging()
    args = build_parser().parse_args(argv)
    try:
        result: int = args.handler(args)
    except ConfigurationError as error:
        return _fail(str(error))
    return result
