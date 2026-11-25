import uuid
from dataclasses import dataclass
from datetime import datetime

import jwt

ALGORITHM = "HS256"
ISSUER = "toutbet"
AUDIENCE = "toutbet-api"
ACCESS_TOKEN_TTL_SECONDS = 900
CLOCK_SKEW_SECONDS = 30
REQUIRED_CLAIMS = ["sub", "sid", "jti", "iat", "exp", "iss", "aud"]


class InvalidTokenError(Exception):
    pass


@dataclass(frozen=True)
class AccessClaims:
    user_id: uuid.UUID
    session_id: uuid.UUID
    jti: uuid.UUID


def issue_access_token(
    secret: str, user_id: uuid.UUID, session_id: uuid.UUID, jti: uuid.UUID, now: datetime
) -> str:
    issued_at = int(now.timestamp())
    claims = {
        "sub": str(user_id),
        "sid": str(session_id),
        "jti": str(jti),
        "iat": issued_at,
        "exp": issued_at + ACCESS_TOKEN_TTL_SECONDS,
        "iss": ISSUER,
        "aud": AUDIENCE,
    }
    return jwt.encode(claims, secret, algorithm=ALGORITHM)


def decode_access_token(token: str, secret: str, now: datetime) -> AccessClaims:
    try:
        claims = jwt.decode(
            token,
            secret,
            algorithms=[ALGORITHM],
            audience=AUDIENCE,
            issuer=ISSUER,
            # exp/iat are checked below with the injected Clock; PyJWT would use the system clock.
            options={"require": REQUIRED_CLAIMS, "verify_exp": False, "verify_iat": False},
        )
    except jwt.PyJWTError as error:
        raise InvalidTokenError("signature or claims rejected") from error
    if set(claims) != set(REQUIRED_CLAIMS):
        raise InvalidTokenError("unexpected claims")
    _check_lifetime(claims.get("iat"), claims.get("exp"), int(now.timestamp()))
    try:
        return AccessClaims(
            user_id=uuid.UUID(str(claims["sub"])),
            session_id=uuid.UUID(str(claims["sid"])),
            jti=uuid.UUID(str(claims["jti"])),
        )
    except ValueError as error:
        raise InvalidTokenError("malformed identifiers") from error


def _check_lifetime(issued_at: object, expires_at: object, now: int) -> None:
    if type(issued_at) is not int or type(expires_at) is not int:
        raise InvalidTokenError("time claims must be integers")
    if expires_at <= now:
        raise InvalidTokenError("expired")
    if issued_at > now + CLOCK_SKEW_SECONDS:
        raise InvalidTokenError("issued in the future")
    if expires_at - issued_at > ACCESS_TOKEN_TTL_SECONDS:
        raise InvalidTokenError("lifetime too long")
