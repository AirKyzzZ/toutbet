from typing import Annotated, Any, Literal

from pydantic import Field, StrictInt, StringConstraints

from toutbet.schemas.common import RequestModel

EventId = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9_-]{1,128}$")]
Reference = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9_-]{1,128}$")]


class PaymentData(RequestModel):
    session_id: Reference
    reference: Reference | None = None
    amount_cents: Annotated[StrictInt, Field(gt=0)]
    currency: Annotated[str, StringConstraints(pattern=r"^[A-Z]{3}$")]


class KycData(RequestModel):
    reference: Reference


class PaymentEvent(RequestModel):
    event_id: EventId
    type: Literal["payment.succeeded", "payment.failed"]
    data: PaymentData


class KycEvent(RequestModel):
    event_id: EventId
    type: Literal["kyc.verified", "kyc.rejected"]
    data: KycData


class WebhookEnvelope(RequestModel):
    event_id: EventId
    type: Literal["payment.succeeded", "payment.failed", "kyc.verified", "kyc.rejected"]
    data: dict[str, Any]
