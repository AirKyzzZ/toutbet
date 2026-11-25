import uuid
from datetime import datetime
from typing import Annotated

from pydantic import AwareDatetime, BaseModel, Field, StrictInt, StringConstraints

from toutbet.schemas.common import Outcome, RequestModel, ResultOutcome, StakeAmountCents


class BetCreate(RequestModel):
    title: Annotated[str, StringConstraints(min_length=5, max_length=140)]
    description: Annotated[str, StringConstraints(max_length=1000)] | None = None
    event_starts_at: AwareDatetime
    closes_at: AwareDatetime
    bookie_commission_bps: Annotated[StrictInt, Field(ge=0, le=1000)]
    min_stake_cents: Annotated[StrictInt, Field(ge=100, le=50_000)] | None = None


class OutcomeTotals(BaseModel):
    OUI: int
    NON: int


class MyStake(BaseModel):
    id: uuid.UUID
    outcome: str
    amount_cents: int
    created_at: datetime


class DecisionView(BaseModel):
    moderator: str
    outcome: str
    justification: str
    created_at: datetime


class BetView(BaseModel):
    id: uuid.UUID
    title: str
    description: str | None
    bookie: str
    my_role: str
    status: str
    event_starts_at: datetime
    closes_at: datetime
    bookie_commission_bps: int
    platform_commission_bps: int
    min_stake_cents: int | None
    totals: OutcomeTotals
    participants: list[str]
    my_stake: MyStake | None
    my_contested: bool
    proposed_outcome: str | None
    proposal_note: str | None
    contest_deadline: datetime | None
    final_outcome: str | None
    bookie_fee_forfeited: bool
    settled_at: datetime | None
    decision: DecisionView | None


class BetPage(BaseModel):
    page: int
    page_size: int
    items: list[BetView]


class StakeCreate(RequestModel):
    outcome: Outcome
    amount_cents: StakeAmountCents


class Receipt(BaseModel):
    payload: str
    signature: str
    key_id: str
    algorithm: str = "Ed25519"


class StakeResponse(BaseModel):
    stake: MyStake
    bet_id: uuid.UUID
    ledger_hash: str
    receipt: Receipt


class ResultProposal(RequestModel):
    outcome: ResultOutcome
    note: Annotated[str, StringConstraints(max_length=500)] | None = None


class ContestCreate(RequestModel):
    reason: Annotated[str, StringConstraints(min_length=10, max_length=500)]


class InvitationCreated(BaseModel):
    invitation_url: str
    expires_at: datetime


class InvitationAccept(RequestModel):
    token: Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9_-]{43}$")]


class InvitationAccepted(BaseModel):
    bet_id: uuid.UUID


class PublicKeyResponse(BaseModel):
    key_id: str
    algorithm: str
    public_key: str
