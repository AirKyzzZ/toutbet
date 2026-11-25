import hashlib
import hmac
import re

SIGNATURE_HEADER = "X-PSP-Signature"
TOLERANCE_SECONDS = 300
MAX_HEADER_LENGTH = 200
_HEADER_PATTERN = re.compile(r"t=(\d{1,12}),v1=([0-9a-f]{64})")


def compute_signature(secret: bytes, timestamp: int, body: bytes) -> str:
    message = str(timestamp).encode("ascii") + b"." + body
    return hmac.new(secret, message, hashlib.sha256).hexdigest()


def sign_webhook(secret: bytes, timestamp: int, body: bytes) -> str:
    return f"t={timestamp},v1={compute_signature(secret, timestamp, body)}"


def verify_webhook(secret: bytes, header: str | None, body: bytes, now: int) -> bool:
    if header is None or len(header) > MAX_HEADER_LENGTH:
        return False
    match = _HEADER_PATTERN.fullmatch(header.strip())
    if match is None:
        return False
    timestamp = int(match.group(1))
    if abs(now - timestamp) > TOLERANCE_SECONDS:
        return False
    expected = compute_signature(secret, timestamp, body)
    return hmac.compare_digest(expected, match.group(2))
