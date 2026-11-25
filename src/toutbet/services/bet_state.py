import uuid
from typing import Any, cast

from sqlalchemy import CursorResult, update
from sqlalchemy.orm import Session

from toutbet.errors import ApiError, ErrorCode
from toutbet.models import Bet


class InvalidTransitionError(Exception):
    pass


def transition(db: Session, bet_id: uuid.UUID, expected: str, target: str, **values: Any) -> None:
    statement = (
        update(Bet).where(Bet.id == bet_id, Bet.status == expected).values(status=target, **values)
    )
    result = cast(CursorResult[Any], db.execute(statement))
    if result.rowcount != 1:
        raise InvalidTransitionError(f"bet is not {expected}")


def transition_or_conflict(
    db: Session, bet_id: uuid.UUID, expected: str, target: str, **values: Any
) -> None:
    try:
        transition(db, bet_id, expected, target, **values)
    except InvalidTransitionError:
        raise ApiError(409, ErrorCode.INVALID_STATE) from None
