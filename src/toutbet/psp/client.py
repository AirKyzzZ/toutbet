from dataclasses import dataclass
from typing import Annotated, Any, Protocol

import httpx
from fastapi import Request
from pydantic import BaseModel, ConfigDict, StringConstraints, ValidationError

PSP_TIMEOUT = httpx.Timeout(5.0, connect=2.0)
SafeId = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9_-]{1,128}$")]
HostedUrl = Annotated[str, StringConstraints(pattern=r"^https?://[^\s]{1,2000}$")]


class PspUnavailableError(Exception):
    pass


class PspRefusedError(Exception):
    pass


@dataclass(frozen=True)
class HostedSession:
    session_id: str
    redirect_url: str


@dataclass(frozen=True)
class Payout:
    payout_id: str


class PspClient(Protocol):
    def create_checkout(
        self, *, reference: str, amount_cents: int, currency: str, return_url: str
    ) -> HostedSession: ...

    def create_kyc_session(self, *, reference: str, return_url: str) -> HostedSession: ...

    def create_payout(
        self, *, reference: str, user_reference: str, amount_cents: int, currency: str
    ) -> Payout: ...


class _HostedSessionBody(BaseModel):
    model_config = ConfigDict(extra="ignore")
    session_id: SafeId
    redirect_url: HostedUrl


class _PayoutBody(BaseModel):
    model_config = ConfigDict(extra="ignore")
    payout_id: SafeId


class HttpPspClient:
    def __init__(
        self, base_url: str, api_key: str, transport: httpx.BaseTransport | None = None
    ) -> None:
        self._client = httpx.Client(
            base_url=base_url,
            timeout=PSP_TIMEOUT,
            headers={"Authorization": f"Bearer {api_key}"},
            follow_redirects=False,
            transport=transport,
        )

    @property
    def base_url(self) -> str:
        return str(self._client.base_url)

    @property
    def timeout(self) -> httpx.Timeout:
        return self._client.timeout

    def close(self) -> None:
        self._client.close()

    def create_checkout(
        self, *, reference: str, amount_cents: int, currency: str, return_url: str
    ) -> HostedSession:
        body = self._post(
            "/v1/checkout_sessions",
            {
                "reference": reference,
                "amount_cents": amount_cents,
                "currency": currency,
                "return_url": return_url,
            },
        )
        parsed = self._parse(_HostedSessionBody, body)
        return HostedSession(parsed.session_id, parsed.redirect_url)

    def create_kyc_session(self, *, reference: str, return_url: str) -> HostedSession:
        body = self._post("/v1/kyc_sessions", {"reference": reference, "return_url": return_url})
        parsed = self._parse(_HostedSessionBody, body)
        return HostedSession(parsed.session_id, parsed.redirect_url)

    def create_payout(
        self, *, reference: str, user_reference: str, amount_cents: int, currency: str
    ) -> Payout:
        body = self._post(
            "/v1/payouts",
            {
                "reference": reference,
                "user_reference": user_reference,
                "amount_cents": amount_cents,
                "currency": currency,
            },
        )
        return Payout(self._parse(_PayoutBody, body).payout_id)

    def _post(self, path: str, payload: dict[str, Any]) -> Any:
        try:
            response = self._client.post(path, json=payload)
        except httpx.HTTPError as error:
            raise PspUnavailableError(type(error).__name__) from error
        if response.status_code >= 500:
            raise PspUnavailableError(f"status {response.status_code}")
        if response.status_code >= 400:
            raise PspRefusedError(f"status {response.status_code}")
        try:
            return response.json()
        except ValueError as error:
            raise PspUnavailableError("invalid JSON") from error

    @staticmethod
    def _parse[Model: BaseModel](model: type[Model], body: Any) -> Model:
        try:
            return model.model_validate(body)
        except ValidationError as error:
            raise PspUnavailableError("unexpected response") from error


def get_psp_client(request: Request) -> PspClient:
    client: PspClient = request.app.state.psp_client
    return client
