import hmac
from typing import Annotated

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import HTMLResponse, Response

from mock_psp.models import (
    CheckoutRequest,
    KycRequest,
    PayoutRecord,
    PayoutRequest,
    SafeId,
    Store,
    new_id,
)
from mock_psp.pages import STYLESHEET, back_link, button_form, page, paragraph
from mock_psp.settings import MockPspSettings
from mock_psp.webhooks import send_webhook
from toutbet.observability import log_event


def register_api(app: FastAPI, settings: MockPspSettings, store: Store) -> None:
    def require_api_key(authorization: Annotated[str | None, Header()] = None) -> None:
        expected = f"Bearer {settings.api_key.get_secret_value()}"
        if authorization is None or not hmac.compare_digest(authorization, expected):
            raise HTTPException(status_code=401)

    protected = [Depends(require_api_key)]

    @app.post("/v1/checkout_sessions", dependencies=protected)
    def create_checkout(body: CheckoutRequest) -> dict[str, str]:
        session_id = new_id("cs")
        store.checkouts[session_id] = body
        return {"session_id": session_id, "redirect_url": f"{settings.public_url}/pay/{session_id}"}

    @app.post("/v1/kyc_sessions", dependencies=protected)
    def create_kyc(body: KycRequest) -> dict[str, str]:
        session_id = new_id("kyc")
        store.kyc[session_id] = body
        return {"session_id": session_id, "redirect_url": f"{settings.public_url}/kyc/{session_id}"}

    @app.post("/v1/payouts", dependencies=protected)
    def create_payout(
        body: PayoutRequest, idempotency_key: Annotated[SafeId, Header(alias="Idempotency-Key")]
    ) -> dict[str, str]:
        status = "refused" if settings.refuse_payouts else "paid"
        candidate = PayoutRecord(body.reference, new_id("po"), status)
        record = store.payouts.setdefault(idempotency_key, candidate)
        if record is candidate:
            log_event(
                "mock_psp.payout",
                reference=body.reference,
                amount_cents=body.amount_cents,
                status=status,
            )
        if record.status == "refused":
            raise HTTPException(status_code=402)
        return {"payout_id": record.payout_id}

    @app.get("/v1/payouts/{reference}", dependencies=protected)
    def find_payout(reference: SafeId) -> dict[str, str]:
        for record in list(store.payouts.values()):
            if record.reference == reference:
                return {"payout_id": record.payout_id, "status": record.status}
        raise HTTPException(status_code=404)


def register_pages(app: FastAPI, settings: MockPspSettings, store: Store) -> None:
    @app.get("/pay/{session_id}", response_class=HTMLResponse)
    def payment_page(session_id: SafeId) -> str:
        checkout = store.checkouts.get(session_id)
        if checkout is None:
            raise HTTPException(status_code=404)
        amount = (
            f"{checkout.amount_cents // 100},{checkout.amount_cents % 100:02d} {checkout.currency}"
        )
        return page(
            "Paiement",
            paragraph(f"Montant : {amount}. Aucune carte n'est demandée sur ce PSP de test."),
            button_form(f"/pay/{session_id}/confirm", "Payer"),
            button_form(f"/pay/{session_id}/refuse", "Refuser", "refuse"),
        )

    @app.post("/pay/{session_id}/{decision}", response_class=HTMLResponse)
    def payment_decision(session_id: SafeId, decision: str) -> str:
        if decision not in {"confirm", "refuse"}:
            raise HTTPException(status_code=404)
        checkout = store.checkouts.pop(session_id, None)
        if checkout is None:
            raise HTTPException(status_code=404)
        event_type = "payment.succeeded" if decision == "confirm" else "payment.failed"
        data = {
            "session_id": session_id,
            "reference": checkout.reference,
            "amount_cents": checkout.amount_cents,
            "currency": checkout.currency,
        }
        delivered = send_webhook(settings, event_type, data)
        title = "Paiement accepté" if decision == "confirm" else "Paiement refusé"
        note = "ToutBet' a été notifié." if delivered else "La notification a échoué."
        return page(title, paragraph(note), back_link(checkout.return_url))

    @app.get("/kyc/{session_id}", response_class=HTMLResponse)
    def kyc_page(session_id: SafeId) -> str:
        if session_id not in store.kyc:
            raise HTTPException(status_code=404)
        return page(
            "Vérification d'identité",
            paragraph("Simulation : aucune pièce d'identité n'est demandée."),
            button_form(f"/kyc/{session_id}/verify", "Valider mon identité"),
        )

    @app.post("/kyc/{session_id}/verify", response_class=HTMLResponse)
    def kyc_verify(session_id: SafeId) -> str:
        request = store.kyc.pop(session_id, None)
        if request is None:
            raise HTTPException(status_code=404)
        delivered = send_webhook(settings, "kyc.verified", {"reference": request.reference})
        note = "Identité vérifiée." if delivered else "La notification a échoué."
        return page("Vérification terminée", paragraph(note), back_link(request.return_url))

    @app.get("/assets/mock.css")
    def stylesheet() -> Response:
        return Response(STYLESHEET, media_type="text/css")
