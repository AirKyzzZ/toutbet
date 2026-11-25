from datetime import UTC, datetime
from typing import Annotated, Literal

from pydantic import (
    AfterValidator,
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    StrictInt,
    field_validator,
)

Outcome = Literal["OUI", "NON"]
ResultOutcome = Literal["OUI", "NON", "ANNULE"]
StakeAmountCents = Annotated[StrictInt, Field(ge=100, le=50_000)]
PageNumber = Annotated[int, Field(ge=1, le=10_000)]
PageSize = Annotated[int, Field(ge=1, le=50)]
MAX_PAGE_SIZE = 50


def min_visible_chars(minimum: int) -> AfterValidator:
    def check(value: str) -> str:
        if sum(not char.isspace() for char in value) < minimum:
            raise ValueError(f"must contain at least {minimum} non-whitespace characters")
        return value

    return AfterValidator(check)


def to_utc(value: datetime) -> datetime:
    try:
        return value.astimezone(UTC)
    except OverflowError:
        raise ValueError("must stay within years 1 to 9999 once converted to UTC") from None


UtcDatetime = Annotated[AwareDatetime, AfterValidator(to_utc)]


class RequestModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    @field_validator("*")
    @classmethod
    def reject_nul_characters(cls, value: object) -> object:
        if isinstance(value, str) and "\x00" in value:
            raise ValueError("must not contain NUL characters")
        return value


class StatusResponse(BaseModel):
    status: str
