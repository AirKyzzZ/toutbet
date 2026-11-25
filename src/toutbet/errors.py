from collections.abc import Mapping
from enum import StrEnum

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.responses import Response


class ErrorCode(StrEnum):
    BAD_REQUEST = "BAD_REQUEST"
    INVALID_SIGNATURE = "INVALID_SIGNATURE"
    UNAUTHENTICATED = "UNAUTHENTICATED"
    FORBIDDEN = "FORBIDDEN"
    KYC_REQUIRED = "KYC_REQUIRED"
    BOOKIE_CANNOT_STAKE = "BOOKIE_CANNOT_STAKE"
    NOT_FOUND = "NOT_FOUND"
    METHOD_NOT_ALLOWED = "METHOD_NOT_ALLOWED"
    INSUFFICIENT_FUNDS = "INSUFFICIENT_FUNDS"
    BET_CLOSED = "BET_CLOSED"
    INVALID_STATE = "INVALID_STATE"
    ALREADY_STAKED = "ALREADY_STAKED"
    ALREADY_CONTESTED = "ALREADY_CONTESTED"
    CONTEST_WINDOW_CLOSED = "CONTEST_WINDOW_CLOSED"
    INVITATION_LIMIT = "INVITATION_LIMIT"
    IDEMPOTENCY_IN_PROGRESS = "IDEMPOTENCY_IN_PROGRESS"
    PAYLOAD_TOO_LARGE = "PAYLOAD_TOO_LARGE"
    VALIDATION_ERROR = "VALIDATION_ERROR"
    IDEMPOTENCY_KEY_REUSED = "IDEMPOTENCY_KEY_REUSED"
    RATE_LIMITED = "RATE_LIMITED"
    INTERNAL_ERROR = "INTERNAL_ERROR"
    PSP_UNAVAILABLE = "PSP_UNAVAILABLE"
    WITHDRAWAL_PENDING = "WITHDRAWAL_PENDING"
    FEATURE_DISABLED = "FEATURE_DISABLED"


MESSAGES: dict[ErrorCode, str] = {
    ErrorCode.BAD_REQUEST: "Requête invalide.",
    ErrorCode.INVALID_SIGNATURE: "Requête invalide.",
    ErrorCode.UNAUTHENTICATED: "Authentification requise ou identifiants invalides.",
    ErrorCode.FORBIDDEN: "Action non autorisée.",
    ErrorCode.KYC_REQUIRED: "Vérification d'identité requise.",
    ErrorCode.BOOKIE_CANNOT_STAKE: "Le bookie ne peut pas participer à son propre pari.",
    ErrorCode.NOT_FOUND: "Ressource introuvable.",
    ErrorCode.METHOD_NOT_ALLOWED: "Méthode non autorisée.",
    ErrorCode.INSUFFICIENT_FUNDS: "Solde insuffisant.",
    ErrorCode.BET_CLOSED: "Les mises sont closes pour ce pari.",
    ErrorCode.INVALID_STATE: "Action impossible dans l'état actuel du pari.",
    ErrorCode.ALREADY_STAKED: "Vous avez déjà misé sur ce pari.",
    ErrorCode.ALREADY_CONTESTED: "Vous avez déjà contesté ce résultat.",
    ErrorCode.CONTEST_WINDOW_CLOSED: "La fenêtre de contestation est close.",
    ErrorCode.INVITATION_LIMIT: "Trop d'invitations actives pour ce pari.",
    ErrorCode.IDEMPOTENCY_IN_PROGRESS: "Une requête avec cette clé est en cours de traitement.",
    ErrorCode.PAYLOAD_TOO_LARGE: "Requête trop volumineuse.",
    ErrorCode.VALIDATION_ERROR: "Données invalides.",
    ErrorCode.IDEMPOTENCY_KEY_REUSED: "Cette clé d'idempotence a déjà servi ailleurs.",
    ErrorCode.RATE_LIMITED: "Trop de requêtes, réessayez plus tard.",
    ErrorCode.INTERNAL_ERROR: "Erreur interne.",
    ErrorCode.PSP_UNAVAILABLE: "Le prestataire de paiement est indisponible.",
    ErrorCode.WITHDRAWAL_PENDING: (
        "Retrait en attente de confirmation du prestataire de paiement. Le montant reste "
        "réservé, il sera versé ou recrédité automatiquement."
    ),
    ErrorCode.FEATURE_DISABLED: "Fonctionnalité temporairement désactivée.",
}

INPUT_ECHOING_ERRORS: dict[str, str] = {"uuid_parsing": "Input should be a valid UUID"}

HTTP_STATUS_CODES: dict[int, ErrorCode] = {
    404: ErrorCode.NOT_FOUND,
    405: ErrorCode.METHOD_NOT_ALLOWED,
    413: ErrorCode.PAYLOAD_TOO_LARGE,
}


class ApiError(Exception):
    def __init__(
        self,
        status_code: int,
        code: ErrorCode,
        message: str | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        super().__init__(code.value)
        self.status_code = status_code
        self.code = code
        self.message = message or MESSAGES[code]
        self.headers = headers


def error_payload(
    code: ErrorCode, request_id: str, message: str | None = None
) -> dict[str, dict[str, object]]:
    return {
        "error": {
            "code": code.value,
            "message": message or MESSAGES[code],
            "request_id": request_id,
        }
    }


def request_id_of(request: Request) -> str:
    return str(getattr(request.state, "request_id", ""))


def error_response(
    request: Request,
    status_code: int,
    code: ErrorCode,
    message: str | None = None,
    headers: Mapping[str, str] | None = None,
) -> JSONResponse:
    return JSONResponse(
        error_payload(code, request_id_of(request), message),
        status_code=status_code,
        headers=headers,
    )


def _api_error_handler(request: Request, exc: Exception) -> Response:
    if not isinstance(exc, ApiError):
        raise exc
    return error_response(request, exc.status_code, exc.code, exc.message, exc.headers)


def _http_error_handler(request: Request, exc: Exception) -> Response:
    if not isinstance(exc, StarletteHTTPException):
        raise exc
    code = HTTP_STATUS_CODES.get(exc.status_code, ErrorCode.BAD_REQUEST)
    if exc.status_code >= 500:
        code = ErrorCode.INTERNAL_ERROR
    return error_response(request, exc.status_code, code, headers=exc.headers)


def _validation_error_handler(request: Request, exc: Exception) -> Response:
    if not isinstance(exc, RequestValidationError):
        raise exc
    fields = [
        {
            "loc": [str(part) for part in item.get("loc", ())],
            "msg": INPUT_ECHOING_ERRORS.get(str(item.get("type")), str(item.get("msg", ""))),
        }
        for item in exc.errors()
    ]
    payload = error_payload(ErrorCode.VALIDATION_ERROR, request_id_of(request))
    payload["error"]["fields"] = fields
    return JSONResponse(payload, status_code=422)


def register_exception_handlers(app: FastAPI) -> None:
    app.add_exception_handler(ApiError, _api_error_handler)
    app.add_exception_handler(StarletteHTTPException, _http_error_handler)
    app.add_exception_handler(RequestValidationError, _validation_error_handler)
