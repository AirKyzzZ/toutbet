import ast
import uuid
from datetime import UTC, date, datetime
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from toutbet.models import LedgerEntry
from toutbet.services.accounts import create_user
from toutbet.services.ledger import SYSTEM_TRACE
from toutbet.services.money import (
    PSP_CLEARING,
    InsufficientFundsError,
    balance_of,
    escrow_account,
    transfer,
    wallet_account,
)

NOW = datetime(2026, 11, 25, 12, 0, tzinfo=UTC)
SOURCE = Path(__file__).parents[2] / "src" / "toutbet"


def _wallet_with(db: Session, amount: int) -> uuid.UUID:
    with db.begin():
        user = create_user(
            db,
            email="money@example.org",
            display_name="money_user",
            password_hash="$argon2id$placeholder",
            birth_date=date(1990, 1, 1),
            role="user",
            now=NOW,
        )
        transfer(
            db,
            entry_type="deposit_test",
            source=PSP_CLEARING,
            target=wallet_account(user.id),
            amount_cents=amount,
            now=NOW,
            trace=SYSTEM_TRACE,
        )
    return user.id


def test_debit_refused_when_balance_insufficient(db_session: Session) -> None:
    user_id = _wallet_with(db_session, 500)

    with pytest.raises(InsufficientFundsError), db_session.begin():
        transfer(
            db_session,
            entry_type="stake",
            source=wallet_account(user_id),
            target=escrow_account(uuid.uuid4()),
            amount_cents=501,
            now=NOW,
            trace=SYSTEM_TRACE,
        )

    with db_session.begin():
        assert balance_of(db_session, user_id) == 500
        assert len(db_session.scalars(select(LedgerEntry)).all()) == 1


@pytest.mark.parametrize("amount", [0, -1, 10.5, True])
def test_transfer_rejects_non_positive_or_non_integer_amounts(
    db_session: Session, amount: object
) -> None:
    user_id = _wallet_with(db_session, 500)

    with pytest.raises(ValueError, match="positive integer"), db_session.begin():
        transfer(
            db_session,
            entry_type="stake",
            source=wallet_account(user_id),
            target=PSP_CLEARING,
            amount_cents=amount,
            now=NOW,
            trace=SYSTEM_TRACE,
        )


def _writes_balance(node: ast.AST) -> bool:
    if isinstance(node, ast.Assign | ast.AugAssign | ast.AnnAssign):
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        return any(isinstance(t, ast.Attribute) and t.attr == "balance_cents" for t in targets)
    if isinstance(node, ast.Call):
        called = node.func
        name = called.attr if isinstance(called, ast.Attribute) else getattr(called, "id", "")
        keywords = {keyword.arg for keyword in node.keywords}
        return name in {"values", "Wallet", "update"} and "balance_cents" in keywords
    return False


def test_only_money_module_writes_balances() -> None:
    offenders = sorted(
        path.relative_to(SOURCE).as_posix()
        for path in SOURCE.rglob("*.py")
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8")))
        if _writes_balance(node)
    )

    assert set(offenders) == {"services/money.py"}


def test_money_code_has_no_float_or_decimal() -> None:
    money_files = ["services/money.py", "services/settlement.py", "services/ledger.py"]

    for name in money_files:
        tree = ast.parse((SOURCE / name).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            assert not (isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div)), name
            assert not (isinstance(node, ast.Constant) and isinstance(node.value, float)), name
            assert not (isinstance(node, ast.Name) and node.id in {"float", "Decimal"}), name
