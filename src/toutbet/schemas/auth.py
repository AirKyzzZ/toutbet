import uuid
from datetime import date
from typing import Annotated, Final, Literal

from pydantic import BaseModel, BeforeValidator, StringConstraints

from toutbet.schemas.common import RequestModel

EMAIL_PATTERN = (
    r"^[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]{1,64}@"
    r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?"
    r"(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)+$"
)


def normalize_email(value: object) -> object:
    if isinstance(value, str):
        return value.strip().lower()
    return value


Email = Annotated[
    str,
    BeforeValidator(normalize_email),
    StringConstraints(max_length=254, pattern=EMAIL_PATTERN),
]
DisplayName = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9_-]{3,30}$")]
NewPassword = Annotated[str, StringConstraints(min_length=12, max_length=128)]
SubmittedPassword = Annotated[str, StringConstraints(min_length=1, max_length=128)]


class RegisterRequest(RequestModel):
    email: Email
    display_name: DisplayName
    password: NewPassword
    birth_date: date


class LoginRequest(RequestModel):
    email: Annotated[str, BeforeValidator(normalize_email), StringConstraints(max_length=254)]
    password: SubmittedPassword


BEARER: Final = "bearer"


class TokenResponse(BaseModel):
    access_token: str
    token_type: Literal["bearer"] = BEARER
    expires_in: int


class MeResponse(BaseModel):
    id: uuid.UUID
    email: str
    display_name: str
    role: str
    kyc_status: str
