import base64
import json
import time
import uuid
from collections.abc import Callable
from typing import Any

from browser_flows import (
    BAD_CREDENTIALS,
    INVALID_DATA,
    NOT_FOUND,
    Api,
    Phone,
    close_betting,
    new_account,
    tampered_json,
)
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from playwright.sync_api import expect

STAKE_TAMPERING: list[dict[str, Any]] = [
    {"amount_cents": -500},
    {"amount_cents": 0},
    {"amount_cents": 99},
    {"amount_cents": 50_001},
    {"amount_cents": 10.5},
    {"amount_cents": "1000"},
    {"amount_cents": True},
    {"amount_cents": 10**15},
    {"outcome": "PEUT-ETRE"},
    {"user_id": str(uuid.uuid4())},
    {"bet_id": str(uuid.uuid4())},
    {"receipt": "forged"},
]


def _unpadded(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def _participant(api: Api, phone: Phone, funds_cents: int = 2000) -> tuple[str, str]:
    bookie = api.signup(new_account("bookie"))
    bet_id = api.create_bet(bookie)
    account = new_account("bettor")
    token = api.signup(account)
    api.fund(token, funds_cents)
    api.join(token, api.invitation_url(bookie, bet_id))
    phone.login(account)
    return bet_id, bookie


def test_e2e_as02_coming_back_from_psp_without_paying_credits_nothing(phone: Phone) -> None:
    phone.signup(new_account())

    phone.start_deposit("20")
    phone.page.go_back()
    phone.expect_balance(0)
    phone.start_deposit("20")
    phone.page.get_by_role("button", name="Refuser").click()
    expect(phone.page.get_by_text("Paiement refusé")).to_be_visible()
    phone.page.get_by_role("link", name="Retour à ToutBet'").click()

    phone.expect_balance(0)
    expect(phone.view.get_by_text("Aucun mouvement.")).to_be_visible()


def test_e2e_as02_webhooks_forged_from_the_browser_credit_nothing(phone: Phone, api: Api) -> None:
    phone.signup(new_account())
    deposit = phone.start_deposit("20")
    session_id = deposit["redirect_url"].rsplit("/", 1)[1]
    phone.open("#/wallet")
    event = {
        "event_id": f"evt_{uuid.uuid4().hex}",
        "type": "payment.succeeded",
        "data": {"session_id": session_id, "amount_cents": 50_000, "currency": "EUR"},
    }
    now = int(time.time())
    _, stale = api.signed_webhook(event, now - 600)
    attempts = [
        {},
        {"X-PSP-Signature": f"t={now},v1={'0' * 64}"},
        {"X-PSP-Signature": stale},
    ]

    statuses = [
        phone.fetch("POST", "/api/webhooks/psp", event, headers)["status"] for headers in attempts
    ]

    assert statuses == [400, 400, 400]
    phone.expect_balance(0)
    genuine = {**event, "data": {**event["data"], "amount_cents": 2000}}
    body, signature = api.signed_webhook(genuine, int(time.time()))
    replays = [
        phone.fetch("POST", "/api/webhooks/psp", body.decode(), {"X-PSP-Signature": signature})
        for _ in range(3)
    ]
    assert [replay["status"] for replay in replays] == [200, 200, 200]
    phone.expect_balance(2000)


def test_e2e_as03_other_players_deposit_and_wallet_stay_private(
    phones: Callable[..., Phone],
) -> None:
    owner = phones()
    owner.signup(new_account("owner"))
    deposit_id = owner.deposit("20")["deposit_id"]
    owner_id = json.loads(owner.fetch("GET", "/api/me")["text"])["id"]
    snoop = phones()
    snoop.signup(new_account("snoop"))

    deposit = snoop.fetch("GET", f"/api/deposits/{deposit_id}")
    wallet = json.loads(snoop.fetch("GET", f"/api/wallet?user_id={owner_id}")["text"])
    unknown = snoop.fetch("GET", f"/api/deposits/{uuid.uuid4()}")

    assert deposit["status"] == unknown["status"] == 404
    hidden = json.loads(deposit["text"])["error"]
    missing = json.loads(unknown["text"])["error"]
    assert (hidden["code"], hidden["message"]) == (missing["code"], missing["message"])
    assert wallet["balance_cents"] == 0
    assert wallet["entries"] == []


def test_e2e_as04_withdrawal_needs_kyc_password_and_no_destination(phone: Phone, api: Api) -> None:
    account = new_account()
    phone.signup(account)
    phone.deposit("20")
    expect(phone.view.get_by_text("Un retrait demande une identité vérifiée")).to_be_visible()
    before_kyc = phone.fetch(
        "POST",
        "/api/withdrawals",
        {"amount_cents": 1000, "password": account.password},
        {"Idempotency-Key": str(uuid.uuid4())},
    )
    assert before_kyc["status"] == 403
    phone.verify_identity()
    page = phone.page
    page.get_by_label("Montant à retirer (€)").fill("10")
    page.get_by_label("Mot de passe", exact=True).fill(account.password)

    for changes, drop, status in (
        ({"iban": "FR7630006000011234567890189"}, (), 422),
        ({"destination": "attacker"}, (), 422),
        ({}, ("password",), 401),
    ):
        page.route("**/api/withdrawals", tampered_json(changes, drop), times=1)
        with page.expect_response("**/api/withdrawals") as info:
            page.get_by_role("button", name="Retirer vers mon compte vérifié").click()
        assert info.value.status == status
        if status == 401:
            break
        expect(phone.flash).to_have_text(INVALID_DATA)
        page.get_by_label("Montant à retirer (€)").fill("10")
        page.get_by_label("Mot de passe", exact=True).fill(account.password)

    expect(phone.flash).to_have_text(BAD_CREDENTIALS)
    assert phone.token() is None
    phone.login(account)
    phone.expect_balance(2000)
    page.get_by_label("Montant à retirer (€)").fill("10")
    page.get_by_label("Mot de passe", exact=True).fill("not my password at all")
    with page.expect_response("**/api/withdrawals") as wrong:
        page.get_by_role("button", name="Retirer vers mon compte vérifié").click()
    assert wrong.value.status == 401
    expect(phone.flash).to_have_text(BAD_CREDENTIALS)
    assert phone.token() is None
    phone.login(account)
    phone.expect_balance(2000)
    page.get_by_label("Montant à retirer (€)").fill("10")
    page.get_by_label("Mot de passe", exact=True).fill(account.password)
    with page.expect_response("**/api/withdrawals") as done:
        page.get_by_role("button", name="Retirer vers mon compte vérifié").click()
    assert done.value.status == 201
    phone.expect_balance(1000)


def test_e2e_as07_tampered_stakes_rejected_and_nothing_debited(phone: Phone, api: Api) -> None:
    bet_id, _ = _participant(api, phone)
    phone.fill_stake(bet_id, "OUI", "10")

    for changes in STAKE_TAMPERING:
        phone.page.route("**/stakes", tampered_json(changes), times=1)
        response = phone.submit_stake()
        assert response.status == 422, changes
        expect(phone.flash).to_have_text(INVALID_DATA)

    phone.page.get_by_label("Mise (€)").fill("25")
    too_much = phone.submit_stake()
    assert too_much.status == 409
    expect(phone.flash).to_have_text("Solde insuffisant.")
    phone.expect_balance(2000)
    phone.open(f"#/bets/{bet_id}")
    expect(phone.view.get_by_text("Ma mise")).to_have_count(0)
    expect(phone.view.get_by_text("Total OUI / NON0,00 € / 0,00 €")).to_have_count(1)


def test_e2e_as07_replayed_stake_debits_once(phone: Phone, api: Api) -> None:
    bet_id, _ = _participant(api, phone)
    phone.fill_stake(bet_id, "NON", "10")
    with phone.page.expect_request("**/stakes") as sent:
        response = phone.submit_stake()
    request = sent.value
    headers = request.all_headers()
    original = response.json()

    replays = [
        phone.page.request.post(request.url, headers=headers, data=request.post_data)
        for _ in range(3)
    ]
    other_body = json.dumps({"outcome": "NON", "amount_cents": 1500})
    reused = phone.page.request.post(request.url, headers=headers, data=other_body)
    fresh_key = {**headers, "idempotency-key": str(uuid.uuid4())}
    second = phone.page.request.post(request.url, headers=fresh_key, data=request.post_data)

    assert response.status == 201
    for replay in replays:
        assert replay.status == 201
        assert replay.headers["idempotent-replayed"] == "true"
        assert replay.json()["stake"]["id"] == original["stake"]["id"]
    assert reused.status == 422
    assert reused.json()["error"]["code"] == "IDEMPOTENCY_KEY_REUSED"
    assert second.status == 409
    assert second.json()["error"]["code"] == "ALREADY_STAKED"
    phone.expect_balance(1000)


def test_e2e_as07_stake_after_closing_refused_before_worker_runs(phone: Phone, api: Api) -> None:
    bet_id, bookie = _participant(api, phone)
    phone.fill_stake(bet_id, "OUI", "10")
    close_betting(api.stack, bet_id)

    response = phone.submit_stake()

    assert response.status == 409
    expect(phone.flash).to_have_text("Les mises sont closes pour ce pari.")
    assert api.bet(bookie, bet_id)["status"] == "open"
    phone.expect_balance(2000)


def test_e2e_as08_client_timestamp_and_bet_edits_refused(phone: Phone, api: Api) -> None:
    bet_id, bookie = _participant(api, phone)
    before = api.bet(bookie, bet_id)
    phone.fill_stake(bet_id, "OUI", "10")

    for field in ("created_at", "placed_at", "closes_at"):
        phone.page.route("**/stakes", tampered_json({field: "2020-01-01T00:00:00Z"}), times=1)
        assert phone.submit_stake().status == 422
    edits = [
        phone.fetch(method, f"/api/bets/{bet_id}", {"closes_at": "2099-01-01T00:00:00Z"})["status"]
        for method in ("PUT", "PATCH", "DELETE")
    ]
    stake_edits = [
        phone.fetch(method, f"/api/bets/{bet_id}/stakes", {"outcome": "NON"})["status"]
        for method in ("PUT", "PATCH", "DELETE")
    ]

    assert edits == [405, 405, 405]
    assert stake_edits == [405, 405, 405]
    after = api.bet(bookie, bet_id)
    assert after["closes_at"] == before["closes_at"]
    assert after["event_starts_at"] == before["event_starts_at"]


def test_e2e_as15_stake_receipt_verifies_with_public_key(phone: Phone, api: Api) -> None:
    bet_id, _ = _participant(api, phone)

    response = phone.stake(bet_id, "OUI", "10")
    key = json.loads(phone.fetch("GET", "/api/receipts/public-key")["text"])

    assert response.status == 201
    body = response.json()
    receipt = body["receipt"]
    payload = json.loads(receipt["payload"])
    me = json.loads(phone.fetch("GET", "/api/me")["text"])
    assert payload["stake_id"] == body["stake"]["id"]
    assert payload["bet_id"] == bet_id
    assert payload["user_id"] == me["id"]
    assert payload["amount_cents"] == 1000
    assert payload["ledger_hash"] == body["ledger_hash"]
    assert receipt["key_id"] == key["key_id"]
    public_key = Ed25519PublicKey.from_public_bytes(_unpadded(key["public_key"]))
    public_key.verify(_unpadded(receipt["signature"]), receipt["payload"].encode())
    forged = receipt["payload"].replace('"amount_cents":1000', '"amount_cents":100000')
    try:
        public_key.verify(_unpadded(receipt["signature"]), forged.encode())
    except InvalidSignature:
        pass
    else:
        raise AssertionError("a forged receipt verified")


def test_e2e_as14_bookie_cannot_stake_or_join_own_bet(phone: Phone) -> None:
    phone.signup(new_account("bookie"))
    phone.deposit("20")
    bet_id = phone.create_bet("Matis va-t-il réussir son créneau ?")
    link = phone.invite(bet_id)
    expect(phone.page.get_by_role("button", name="Miser")).to_have_count(0)

    with phone.page.expect_response("**/api/invitations/accept") as joined:
        phone.follow_link(link)
    stake = phone.fetch(
        "POST",
        f"/api/bets/{bet_id}/stakes",
        {"outcome": "OUI", "amount_cents": 1000},
        {"Idempotency-Key": str(uuid.uuid4())},
    )

    assert joined.value.status == 403
    expect(phone.flash).to_have_text("Le bookie ne peut pas participer à son propre pari.")
    assert stake["status"] == 403
    assert json.loads(stake["text"])["error"]["code"] == "BOOKIE_CANNOT_STAKE"
    phone.expect_balance(2000)


def test_e2e_as03_acting_on_someone_elses_bet_returns_404(
    phones: Callable[..., Phone], api: Api
) -> None:
    phone = phones()
    my_bet, _ = _participant(api, phone)
    owner = api.signup(new_account("owner"))
    their_bet = api.create_bet(owner, "Pari privé des autres")

    phone.fill_stake(my_bet, "OUI", "10")
    phone.page.route(
        f"**/api/bets/{my_bet}/stakes",
        lambda route: route.continue_(url=route.request.url.replace(my_bet, their_bet)),
        times=1,
    )
    stolen_stake = phone.submit_stake()
    attempts = {
        path: phone.fetch("POST", path, body)["status"]
        for path, body in (
            (f"/api/bets/{their_bet}/cancel", None),
            (f"/api/bets/{their_bet}/invitations", None),
            (f"/api/bets/{their_bet}/result", {"outcome": "OUI"}),
            (f"/api/bets/{their_bet}/contests", {"reason": "Je conteste ce résultat."}),
        )
    }
    with phone.page.expect_response(f"**/api/bets/{their_bet}") as read:
        phone.open(f"#/bets/{their_bet}")

    assert stolen_stake.status == 404
    assert set(attempts.values()) == {404}
    assert read.value.status == 404
    expect(phone.flash).to_have_text(NOT_FOUND)
    assert "Pari privé des autres" not in phone.page.content()
    assert api.bet(owner, their_bet)["status"] == "open"
    assert api.bet(owner, their_bet)["participants"] == []
    phone.expect_balance(2000)
