from typing import Annotated, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, StrictInt

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


class RequestModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class StatusResponse(BaseModel):
    status: str
