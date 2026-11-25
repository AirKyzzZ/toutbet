from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictInt

Outcome = Literal["OUI", "NON"]
ResultOutcome = Literal["OUI", "NON", "ANNULE"]
StakeAmountCents = Annotated[StrictInt, Field(ge=100, le=50_000)]
PageNumber = Annotated[int, Field(ge=1, le=10_000)]
PageSize = Annotated[int, Field(ge=1, le=50)]
MAX_PAGE_SIZE = 50


class RequestModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class StatusResponse(BaseModel):
    status: str
