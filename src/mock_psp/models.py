import secrets
from dataclasses import dataclass, field
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StrictInt, StringConstraints

SafeId = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9_-]{1,128}$")]
ReturnUrl = Annotated[str, StringConstraints(pattern=r"^https?://[^\s\"'<>]{1,500}$")]
Currency = Annotated[str, StringConstraints(pattern=r"^[A-Z]{3}$")]
PositiveCents = Annotated[StrictInt, Field(gt=0)]


class StrictBody(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CheckoutRequest(StrictBody):
    reference: SafeId
    amount_cents: PositiveCents
    currency: Currency
    return_url: ReturnUrl


class KycRequest(StrictBody):
    reference: SafeId
    return_url: ReturnUrl


class PayoutRequest(StrictBody):
    reference: SafeId
    user_reference: SafeId
    amount_cents: PositiveCents
    currency: Currency


@dataclass
class Store:
    checkouts: dict[str, CheckoutRequest] = field(default_factory=dict)
    kyc: dict[str, KycRequest] = field(default_factory=dict)


def new_id(prefix: str) -> str:
    return f"{prefix}_{secrets.token_urlsafe(18)}"
