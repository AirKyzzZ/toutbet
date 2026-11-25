import json
from datetime import UTC, datetime
from typing import Any


def canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def iso_utc(moment: datetime) -> str:
    if moment.tzinfo is None:
        raise ValueError("naive datetimes are not allowed")
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
