from collections.abc import Callable
from typing import Any, TypeVar, cast

from fastapi import Request
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from starlette.responses import Response

from toutbet.config import Settings
from toutbet.errors import ErrorCode, error_response
from toutbet.observability import log_event

DEFAULT_RETRY_AFTER_SECONDS = 60

Endpoint = TypeVar("Endpoint", bound=Callable[..., Any])


def client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def ip_key(request: Request) -> str:
    return f"ip:{client_ip(request)}"


def user_key(request: Request) -> str:
    user_id = getattr(request.state, "user_id", None)
    return f"user:{user_id}" if user_id else ip_key(request)


def build_limiter() -> Limiter:
    limiter = Limiter(key_func=ip_key, storage_uri="memory://", headers_enabled=False)
    limiter.enabled = True
    return limiter


class RateLimits:
    def __init__(self, limiter: Limiter, settings: Settings) -> None:
        self.limiter = limiter
        self.settings = settings

    def per_ip(self, limit: str) -> Callable[[Endpoint], Endpoint]:
        return cast(Callable[[Endpoint], Endpoint], self.limiter.limit(limit, key_func=ip_key))

    def per_user(self, limit: str) -> Callable[[Endpoint], Endpoint]:
        return cast(Callable[[Endpoint], Endpoint], self.limiter.limit(limit, key_func=user_key))

    def writes(self) -> Callable[[Endpoint], Endpoint]:
        return self._shared(self.settings.rate_limit_write, "writes")

    def reads(self) -> Callable[[Endpoint], Endpoint]:
        return self._shared(self.settings.rate_limit_read, "reads")

    def _shared(self, limit: str, scope: str) -> Callable[[Endpoint], Endpoint]:
        decorator = self.limiter.shared_limit(limit, scope=scope, key_func=user_key)
        return cast(Callable[[Endpoint], Endpoint], decorator)


def rate_limit_exceeded_handler(request: Request, exc: Exception) -> Response:
    if not isinstance(exc, RateLimitExceeded):
        raise exc
    retry_after = DEFAULT_RETRY_AFTER_SECONDS
    if exc.limit is not None:
        retry_after = int(exc.limit.limit.get_expiry())
    log_event(
        "rate_limit.exceeded",
        client_ip=client_ip(request),
        user_id=getattr(request.state, "user_id", None),
        route=getattr(request.scope.get("route"), "path", None),
    )
    return error_response(
        request, 429, ErrorCode.RATE_LIMITED, headers={"Retry-After": str(retry_after)}
    )
