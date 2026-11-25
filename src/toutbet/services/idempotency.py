import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from fastapi.responses import JSONResponse
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from toutbet.canonical import canonical_json
from toutbet.errors import ApiError, ErrorCode
from toutbet.models import IdempotencyRecord

RETENTION = timedelta(hours=24)


@dataclass(frozen=True)
class StoredResponse:
    status_code: int
    body: str


def request_fingerprint(method: str, path: str, payload: dict[str, Any]) -> str:
    material = f"{method} {path}\n{canonical_json(payload)}"
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def claim(
    db: Session,
    *,
    user_id: uuid.UUID,
    key: str,
    endpoint: str,
    request_hash: str,
    now: datetime,
) -> StoredResponse | None:
    existing = db.get(IdempotencyRecord, (user_id, key))
    if existing is None:
        try:
            with db.begin_nested():
                db.add(
                    IdempotencyRecord(
                        user_id=user_id,
                        idempotency_key=key,
                        endpoint=endpoint,
                        request_hash=request_hash,
                        status_code=None,
                        response_body=None,
                        created_at=now,
                    )
                )
            return None
        except IntegrityError:
            existing = db.execute(
                select(IdempotencyRecord)
                .where(
                    IdempotencyRecord.user_id == user_id,
                    IdempotencyRecord.idempotency_key == key,
                )
                .execution_options(populate_existing=True)
            ).scalar_one()
    return _replay_or_reject(existing, endpoint, request_hash)


def _replay_or_reject(
    record: IdempotencyRecord, endpoint: str, request_hash: str
) -> StoredResponse:
    if record.endpoint != endpoint or record.request_hash != request_hash:
        raise ApiError(422, ErrorCode.IDEMPOTENCY_KEY_REUSED)
    if record.status_code is None or record.response_body is None:
        raise ApiError(409, ErrorCode.IDEMPOTENCY_IN_PROGRESS)
    return StoredResponse(status_code=record.status_code, body=record.response_body)


def complete(
    db: Session, *, user_id: uuid.UUID, key: str, status_code: int, body: dict[str, Any]
) -> None:
    record = db.get(IdempotencyRecord, (user_id, key))
    if record is None:
        raise LookupError("idempotency record not found")
    record.status_code = status_code
    record.response_body = canonical_json(body)
    db.flush()


def release(db: Session, *, user_id: uuid.UUID, key: str) -> None:
    db.execute(
        delete(IdempotencyRecord).where(
            IdempotencyRecord.user_id == user_id, IdempotencyRecord.idempotency_key == key
        )
    )


def purge_expired(db: Session, now: datetime) -> None:
    db.execute(delete(IdempotencyRecord).where(IdempotencyRecord.created_at < now - RETENTION))


def replay_response(stored: StoredResponse) -> JSONResponse:
    return JSONResponse(
        json.loads(stored.body),
        status_code=stored.status_code,
        headers={"Idempotent-Replayed": "true"},
    )
