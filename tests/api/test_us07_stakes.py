import base64
import json

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from fastapi.testclient import TestClient
from sqlalchemy import select

from support import Api, staked_bet
from toutbet.models import LedgerEntry, Stake
from toutbet.services.verification import verify_ledger


def _b64(data: str) -> bytes:
    return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))


def test_us07_1_stake_debits_wallet_credits_escrow_and_chains_ledger(api: Api) -> None:
    scenario = staked_bet(api, [])
    player = api.signup()
    api.fund(player, 3000)
    api.add_participants(scenario.bookie, scenario.bet["id"], player)

    response = api.stake(player, scenario.bet["id"], "OUI", 1000)

    assert response.status_code == 201
    assert api.balance(player) == 2000
    with api.db() as db:
        stake = db.scalars(select(Stake)).one()
        entry = db.scalars(select(LedgerEntry).where(LedgerEntry.entry_type == "stake")).one()
        previous = db.scalars(select(LedgerEntry).where(LedgerEntry.seq == entry.seq - 1)).one()
        assert (entry.from_account, entry.to_account) == (
            f"wallet:{player.id}",
            f"escrow:{scenario.bet['id']}",
        )
        assert entry.prev_hash == previous.hash
        assert stake.ledger_hash == entry.hash
        assert verify_ledger(db).ok
    assert api.bet(player, scenario.bet["id"])["totals"] == {"OUI": 1000, "NON": 0}


def test_us07_2_stake_returns_ed25519_signed_receipt(api: Api, client: TestClient) -> None:
    scenario = staked_bet(api, [])
    player = api.signup()
    api.fund(player, 3000)
    api.add_participants(scenario.bookie, scenario.bet["id"], player)

    body = api.stake(player, scenario.bet["id"], "NON", 1200).json()

    receipt = body["receipt"]
    payload = json.loads(receipt["payload"])
    assert payload["stake_id"] == body["stake"]["id"]
    assert payload["bet_id"] == scenario.bet["id"]
    assert payload["user_id"] == player.id
    assert payload["outcome"] == "NON"
    assert payload["amount_cents"] == 1200
    assert payload["ledger_hash"] == body["ledger_hash"]
    assert payload["created_at"].endswith("Z")
    public = client.get("/api/receipts/public-key").json()
    key = Ed25519PublicKey.from_public_bytes(_b64(public["public_key"]))
    key.verify(_b64(receipt["signature"]), receipt["payload"].encode())
    assert receipt["key_id"] == public["key_id"]


def test_us07_3_insufficient_funds_rejected_without_debit(api: Api) -> None:
    scenario = staked_bet(api, [])
    player = api.signup()
    api.fund(player, 900)
    api.add_participants(scenario.bookie, scenario.bet["id"], player)

    response = api.stake(player, scenario.bet["id"], "OUI", 1000)

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "INSUFFICIENT_FUNDS"
    assert api.balance(player) == 900
    with api.db() as db:
        assert db.scalars(select(Stake)).all() == []
        assert db.scalars(select(LedgerEntry.entry_type)).all() == ["deposit"]


def test_us07_4_one_final_stake_per_participant(api: Api) -> None:
    scenario = staked_bet(api, [("bob", "OUI", 1000)])
    bob = scenario.players["bob"]
    api.fund(bob, 1000)

    second = api.stake(bob, scenario.bet["id"], "NON", 500)

    assert second.status_code == 409
    assert second.json()["error"]["code"] == "ALREADY_STAKED"
    assert api.balance(bob) == 1000
    assert api.bet(bob, scenario.bet["id"])["my_stake"]["outcome"] == "OUI"


def test_us07_5_bookie_and_moderator_cannot_stake(api: Api) -> None:
    scenario = staked_bet(api, [])
    api.fund(scenario.bookie, 2000)
    moderator = api.moderator()

    bookie_attempt = api.stake(scenario.bookie, scenario.bet["id"], "OUI", 1000)
    moderator_attempt = api.stake(moderator, scenario.bet["id"], "OUI", 1000)

    assert bookie_attempt.status_code == moderator_attempt.status_code == 403
    assert bookie_attempt.json()["error"]["code"] == "BOOKIE_CANNOT_STAKE"
    assert api.balance(scenario.bookie) == 2000
