import uuid
from datetime import date, datetime

from sqlalchemy import CheckConstraint, ForeignKey, Index, String, func
from sqlalchemy.orm import Mapped, mapped_column

from toutbet.models.base import Base, uuid_pk

ROLE_USER = "user"
ROLE_MODERATOR = "moderator"
KYC_NONE = "none"
KYC_PENDING = "pending"
KYC_VERIFIED = "verified"
KYC_REJECTED = "rejected"


class User(Base):
    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint("role IN ('user', 'moderator')", name="ck_users_role"),
        CheckConstraint(
            "kyc_status IN ('none', 'pending', 'verified', 'rejected')", name="ck_users_kyc"
        ),
        CheckConstraint("failed_login_count >= 0", name="ck_users_failed_login_count"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    email: Mapped[str] = mapped_column(String(254), unique=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    display_name: Mapped[str] = mapped_column(String(30), unique=True)
    birth_date: Mapped[date]
    role: Mapped[str] = mapped_column(String(16), default=ROLE_USER)
    kyc_status: Mapped[str] = mapped_column(String(16), default=KYC_NONE)
    failed_login_count: Mapped[int] = mapped_column(default=0)
    failed_login_window_started_at: Mapped[datetime | None]
    locked_until: Mapped[datetime | None]
    created_at: Mapped[datetime]


Index("uq_users_display_name_ci", func.lower(User.display_name), unique=True)


class LoginSession(Base):
    __tablename__ = "sessions"

    id: Mapped[uuid.UUID] = uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), index=True)
    current_jti: Mapped[uuid.UUID]
    created_at: Mapped[datetime]
    expires_at: Mapped[datetime]
    revoked_at: Mapped[datetime | None]
