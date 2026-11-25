import os

import pytest
from sqlalchemy import delete, select, update
from sqlalchemy.exc import ProgrammingError

from support import Api
from toutbet.db import make_engine
from toutbet.models import AuditEvent, LedgerEntry, User

pytestmark = pytest.mark.skipif(
    not os.environ.get("DATABASE_URL", "").startswith("postgresql")
    or "DATABASE_ADMIN_URL" not in os.environ,
    reason="needs PostgreSQL with a restricted DATABASE_URL role and DATABASE_ADMIN_URL",
)


def test_pg_app_role_cannot_update_ledger(api: Api) -> None:
    player = api.signup()
    api.fund(player, 1000)
    engine = make_engine(os.environ["DATABASE_URL"])
    forbidden = [
        update(LedgerEntry).values(amount_cents=1),
        delete(LedgerEntry),
        update(AuditEvent).values(payload="{}"),
        delete(AuditEvent),
    ]

    for statement in forbidden:
        with pytest.raises(ProgrammingError, match="permission denied"), engine.begin() as conn:
            conn.execute(statement)

    with engine.begin() as connection:
        assert connection.execute(select(LedgerEntry.amount_cents)).scalars().all() == [1000]
        connection.execute(update(User).values(kyc_status=User.kyc_status))
    with pytest.raises(ProgrammingError), engine.begin() as connection:
        connection.exec_driver_sql("CREATE TABLE intruder (id int)")
    engine.dispose()
