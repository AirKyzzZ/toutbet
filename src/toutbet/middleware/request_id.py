import logging
import time
import uuid
from typing import Any

from starlette.datastructures import MutableHeaders
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from toutbet.errors import ErrorCode, error_payload
from toutbet.observability import REQUEST_ID, log_event


def _route_template(scope: Scope) -> str:
    route = scope.get("route")
    path = getattr(route, "path", None)
    if path is None:
        return "unmatched"
    return str(path) or "/static"


class RequestContextMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        request_id = uuid.uuid4().hex
        state: dict[str, Any] = scope.setdefault("state", {})
        state["request_id"] = request_id
        token = REQUEST_ID.set(request_id)
        started = time.perf_counter()
        outcome = {"status": 500, "response_started": False}

        async def send_with_request_id(message: Message) -> None:
            if message["type"] == "http.response.start":
                outcome["status"] = message["status"]
                outcome["response_started"] = True
                MutableHeaders(scope=message)["X-Request-ID"] = request_id
            await send(message)

        try:
            await self.app(scope, receive, send_with_request_id)
        except Exception:
            log_event("error.unhandled", level=logging.ERROR, exc_info=True)
            if not outcome["response_started"]:
                response = JSONResponse(
                    error_payload(ErrorCode.INTERNAL_ERROR, request_id), status_code=500
                )
                await response(scope, receive, send_with_request_id)
        finally:
            client = scope.get("client")
            log_event(
                "http.request",
                method=scope["method"],
                route=_route_template(scope),
                status=outcome["status"],
                duration_ms=round((time.perf_counter() - started) * 1000, 2),
                client_ip=client[0] if client else None,
                user_id=state.get("user_id"),
            )
            REQUEST_ID.reset(token)
