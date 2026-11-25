import uuid
from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy import update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from toutbet.models import Bet, Invitation, Stake, User, Wallet

NOW = datetime(2026, 11, 25, 12, 0, tzinfo=UTC)


def _user(db: Session) -> User:
    user = User(
        id=uuid.uuid4(),
        email=f"{uuid.uuid4().hex}@example.org",
        password_hash="$argon2id$placeholder",
        display_name=uuid.uuid4().hex[:12],
        birth_date=date(1990, 1, 1),
        created_at=NOW,
    )
    db.add(user)
    db.add(Wallet(user_id=user.id, balance_cents=0))
    db.flush()
    return user


def _bet(db: Session, bookie: User, **overrides: object) -> Bet:
    values: dict[str, object] = {
        "id": uuid.uuid4(),
        "bookie_id": bookie.id,
        "title": "Pari de test",
        "bookie_commission_bps": 400,
        "event_starts_at": NOW + timedelta(hours=2),
        "closes_at": NOW + timedelta(hours=1),
        "created_at": NOW,
        **overrides,
    }
    bet = Bet(**values)
    db.add(bet)
    db.flush()
    return bet


def _stake(db: Session, bet: Bet, user: User, amount: int) -> None:
    db.add(
        Stake(
            id=uuid.uuid4(),
            bet_id=bet.id,
            user_id=user.id,
            outcome="OUI",
            amount_cents=amount,
            ledger_hash="0" * 64,
            created_at=NOW,
        )
    )
    db.flush()


def test_negative_balance_rejected_by_database(db_session: Session) -> None:
    with db_session.begin():
        user = _user(db_session)
        with pytest.raises(IntegrityError):
            db_session.execute(
                update(Wallet).where(Wallet.user_id == user.id).values(balance_cents=-1)
            )


def test_zero_stake_rejected_by_database(db_session: Session) -> None:
    with db_session.begin():
        bookie, player = _user(db_session), _user(db_session)
        bet = _bet(db_session, bookie)
        with pytest.raises(IntegrityError):
            _stake(db_session, bet, player, 0)


def test_closes_at_after_event_start_rejected_by_database(db_session: Session) -> None:
    with db_session.begin():
        bookie = _user(db_session)
        with pytest.raises(IntegrityError):
            _bet(db_session, bookie, closes_at=NOW + timedelta(hours=3))


def test_double_stake_rejected_by_database(db_session: Session) -> None:
    with db_session.begin():
        bookie, player = _user(db_session), _user(db_session)
        bet = _bet(db_session, bookie)
        _stake(db_session, bet, player, 500)
        with pytest.raises(IntegrityError):
            _stake(db_session, bet, player, 700)


@pytest.mark.parametrize(
    "overrides",
    [{"bookie_commission_bps": 1001}, {"platform_commission_bps": 0}, {"status": "paid"}],
)
def test_bet_parameters_bounded_by_database(
    db_session: Session, overrides: dict[str, object]
) -> None:
    with db_session.begin():
        bookie = _user(db_session)
        with pytest.raises(IntegrityError):
            _bet(db_session, bookie, **overrides)


def test_invitation_hash_is_unique(db_session: Session) -> None:
    with db_session.begin():
        bet = _bet(db_session, _user(db_session))
        for _ in range(2):
            db_session.add(
                Invitation(
                    id=uuid.uuid4(),
                    bet_id=bet.id,
                    token_hash="a" * 64,
                    created_at=NOW,
                    expires_at=NOW + timedelta(hours=48),
                )
            )
        with pytest.raises(IntegrityError):
            db_session.flush()


def test_datetimes_round_trip_as_aware_utc(db_session: Session) -> None:
    with db_session.begin():
        bet = _bet(db_session, _user(db_session))
        bet_id = bet.id
    db_session.expunge_all()

    with db_session.begin():
        reloaded = db_session.get(Bet, bet_id)
        assert reloaded is not None
        assert reloaded.closes_at == NOW + timedelta(hours=1)
        assert reloaded.closes_at.tzinfo is not None
        assert reloaded.closes_at.utcoffset() == timedelta(0)
