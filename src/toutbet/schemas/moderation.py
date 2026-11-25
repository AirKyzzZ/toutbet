import uuid
from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, StringConstraints

from toutbet.schemas.bets import OutcomeTotals
from toutbet.schemas.common import RequestModel, ResultOutcome, min_visible_chars


class ContestView(BaseModel):
    contester: str
    reason: str
    created_at: datetime


class DisputeView(BaseModel):
    id: uuid.UUID
    title: str
    description: str | None
    bookie: str
    status: str
    event_starts_at: datetime
    proposed_outcome: str | None
    proposal_note: str | None
    totals: OutcomeTotals
    contests: list[ContestView]


class DisputePage(BaseModel):
    page: int
    page_size: int
    items: list[DisputeView]


class DecisionCreate(RequestModel):
    outcome: ResultOutcome
    justification: Annotated[str, StringConstraints(max_length=1000), min_visible_chars(20)]


class DecisionResult(BaseModel):
    bet_id: uuid.UUID
    outcome: str
    status: str
