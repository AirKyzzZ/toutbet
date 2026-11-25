import json
import logging
import re
import sys
import traceback
from collections.abc import Mapping
from contextvars import ContextVar
from datetime import UTC, datetime
from typing import IO, Any

REQUEST_ID: ContextVar[str | None] = ContextVar("request_id", default=None)
REDACTED = "***"
SENSITIVE_KEY_PARTS = (
    "password",
    "secret",
    "token",
    "authorization",
    "api_key",
    "signature",
    "card",
    "pan",
    "cvv",
    "iban",
)
JWT_PATTERN = re.compile(r"eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]*")
CARD_PATTERN = re.compile(r"(?<![0-9A-Za-z])(?:\d[ -]?){12,18}\d(?![0-9A-Za-z])")
EMAIL_PATTERN = re.compile(r"([A-Za-z0-9._%+-])[A-Za-z0-9._%+-]*@([A-Za-z0-9.-]+\.[A-Za-z]{2,})")
CORE_FIELDS = ("ts", "level", "event", "logger", "request_id")

_LOGGER = logging.getLogger("toutbet")


def is_sensitive_key(key: str) -> bool:
    lowered = key.lower()
    return any(part in lowered for part in SENSITIVE_KEY_PARTS)


def redact_text(text: str) -> str:
    text = JWT_PATTERN.sub(REDACTED, text)
    text = CARD_PATTERN.sub(REDACTED, text)
    return EMAIL_PATTERN.sub(r"\1***@\2", text)


def redact(value: Any, key: str | None = None) -> Any:
    if key is not None and is_sensitive_key(key):
        return REDACTED
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, Mapping):
        return {str(k): redact(v, str(k)) for k, v in value.items()}
    if isinstance(value, list | tuple | set):
        return [redact(item) for item in value]
    return value


class RedactionFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        fields = getattr(record, "fields", None)
        if isinstance(fields, Mapping):
            record.fields = redact(fields)
        record.msg = redact_text(record.getMessage())
        record.args = None
        if record.exc_info and record.exc_info[0] is not None:
            record.exc_text = redact_text("".join(traceback.format_exception(*record.exc_info)))
            record.exc_info = None
        elif record.exc_text:
            record.exc_text = redact_text(record.exc_text)
        return True


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        event = getattr(record, "event", None)
        payload: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname.lower(),
            "event": event or "log",
            "logger": record.name,
            "request_id": REQUEST_ID.get(),
        }
        if event is None:
            payload["message"] = record.getMessage()
        fields = getattr(record, "fields", None)
        if isinstance(fields, Mapping):
            payload.update({k: v for k, v in fields.items() if k not in CORE_FIELDS})
        if record.exc_text:
            payload["exc"] = record.exc_text
        return json.dumps(payload, default=str, ensure_ascii=False)


def build_log_handler(stream: IO[str]) -> logging.Handler:
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JsonFormatter())
    handler.addFilter(RedactionFilter())
    return handler


def configure_logging(level: int = logging.INFO) -> None:
    root = logging.getLogger()
    root.handlers = [build_log_handler(sys.stdout)]
    root.setLevel(level)
    _LOGGER.setLevel(level)
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        uvicorn_logger = logging.getLogger(name)
        uvicorn_logger.handlers = []
        uvicorn_logger.propagate = True
    logging.getLogger("slowapi").setLevel(logging.ERROR)


def log_event(event: str, level: int = logging.INFO, exc_info: bool = False, **fields: Any) -> None:
    _LOGGER.log(level, event, exc_info=exc_info, extra={"event": event, "fields": fields})
