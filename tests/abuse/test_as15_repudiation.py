import base64

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from fastapi.testclient import TestClient
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from support import Api, staked_bet
from toutbet.models import LedgerEntry
from toutbet.security.tokens import decode_access_token
from toutbet.services.verification import verify_ledger


def _b64(data: str) -> bytes:
    return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))


def test_as15_receipt_signature_verifies_with_public_key(api: Api, client: TestClient) -> None:
    scenario = staked_bet(api, [])
    player = api.signup()
    api.fund(player, 2000)
    api.add_participants(scenario.bookie, scenario.bet["id"], player)
    receipt = api.stake(player, scenario.bet["id"], "OUI", 1000).json()["receipt"]
    public = client.get("/api/receipts/public-key").json()
    key = Ed25519PublicKey.from_public_bytes(_b64(public["public_key"]))

    key.verify(_b64(receipt["signature"]), receipt["payload"].encode())

    forged_payload = receipt["payload"].replace('"amount_cents":1000', '"amount_cents":10')
    try:
        key.verify(_b64(receipt["signature"]), forged_payload.encode())
    except InvalidSignature:
        pass
    else:
        raise AssertionError("a modified receipt must not verify")
    assert public["algorithm"] == "Ed25519"


def test_as15_stake_entry_records_session_and_request(api: Api) -> None:
    scenario = staked_bet(api, [])
    player = api.signup()
    api.fund(player, 2000)
    api.add_participants(scenario.bookie, scenario.bet["id"], player)
    key = "6f0c1d1e-5c43-4b8e-9f22-0d3a2a1b9c11"

    response = api.stake(player, scenario.bet["id"], "OUI", 1000, key=key)

    claims = decode_access_token(
        player.token, api.settings.jwt_secret.get_secret_value(), api.clock.now()
    )
    with api.db() as db:
        entry = db.scalars(select(LedgerEntry).where(LedgerEntry.entry_type == "stake")).one()
    assert str(entry.actor_id) == player.id
    assert entry.session_id == claims.session_id
    assert entry.idempotency_key == key
    assert entry.request_id == response.headers["x-request-id"]
    assert entry.created_at == api.clock.now()


def test_as15_verify_ledger_detects_tampering(api: Api, owner_session: Session) -> None:
    staked_bet(api, [("bob", "OUI", 1000), ("carol", "NON", 2000)])
    with api.db() as db:
        assert verify_ledger(db).ok
        target = db.scalars(select(LedgerEntry).where(LedgerEntry.entry_type == "stake")).first()
        assert target is not None
    with owner_session.begin():
        owner_session.execute(
            update(LedgerEntry).where(LedgerEntry.seq == target.seq).values(amount_cents=1)
        )

    with api.db() as db:
        report = verify_ledger(db)

    assert not report.ok
    assert report.message.startswith(f"ledger entry {target.seq}:")
