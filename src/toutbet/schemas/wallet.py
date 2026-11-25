import uuid
from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, Field, StrictInt, StringConstraints

from toutbet.schemas.common import RequestModel


class LedgerLine(BaseModel):
    created_at: datetime
    entry_type: str
    direction: Literal["credit", "debit"]
    amount_cents: int
    bet_id: uuid.UUID | None


class WalletResponse(BaseModel):
    balance_cents: int
    page: int
    page_size: int
    entries: list[LedgerLine]


class DepositCreate(RequestModel):
    amount_cents: Annotated[StrictInt, Field(ge=500, le=50_000)]


class DepositCreated(BaseModel):
    deposit_id: uuid.UUID
    status: str
    redirect_url: str


class DepositView(BaseModel):
    id: uuid.UUID
    amount_cents: int
    currency: str
    status: str
    created_at: datetime


class WithdrawalCreate(RequestModel):
    amount_cents: Annotated[StrictInt, Field(ge=1_000, le=100_000)]
    password: Annotated[str, StringConstraints(max_length=128)] | None = None


class WithdrawalView(BaseModel):
    id: uuid.UUID
    amount_cents: int
    status: str


class RedirectResponse(BaseModel):
    redirect_url: str
