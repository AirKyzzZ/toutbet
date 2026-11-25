import base64
import hashlib
from dataclasses import dataclass
from typing import Any

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from toutbet.canonical import canonical_json

RECEIPT_VERSION = 1


@dataclass(frozen=True)
class SignedReceipt:
    payload: str
    signature: str
    key_id: str


def b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


class ReceiptSigner:
    def __init__(self, seed: bytes) -> None:
        self._private_key = Ed25519PrivateKey.from_private_bytes(seed)
        public_raw = self._private_key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
        self.public_key = b64url(public_raw)
        self.key_id = hashlib.sha256(public_raw).hexdigest()[:16]

    def sign(self, fields: dict[str, Any]) -> SignedReceipt:
        payload = canonical_json({"receipt_version": RECEIPT_VERSION, **fields})
        signature = self._private_key.sign(payload.encode("utf-8"))
        return SignedReceipt(payload=payload, signature=b64url(signature), key_id=self.key_id)
