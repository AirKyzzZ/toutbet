import base64
import json
import secrets
from collections.abc import Callable

from browser_flows import (
    BAD_CREDENTIALS,
    FORBIDDEN,
    INVALID_DATA,
    RATE_LIMITED,
    Account,
    Api,
    Phone,
    new_account,
    seed_account,
    tampered_json,
)
from playwright.sync_api import Route, expect


def _b64(data: dict[str, object]) -> str:
    return base64.urlsafe_b64encode(json.dumps(data).encode()).rstrip(b"=").decode()


def _claims(token: str) -> dict[str, object]:
    payload = token.split(".")[1]
    claims: dict[str, object] = json.loads(base64.urlsafe_b64decode(payload + "=" * 4))
    return claims


def test_e2e_as01_token_kept_out_of_local_storage_cookies_and_urls(phone: Phone) -> None:
    phone.signup(new_account())
    phone.open("#/wallet")
    phone.open("#/bets")
    token = phone.token()

    assert token
    assert phone.page.evaluate("localStorage.length") == 0
    assert phone.page.evaluate("document.cookie") == ""
    assert phone.page.context.cookies() == []
    assert all(token not in url for url in phone.requested_urls)
    assert token not in phone.page.url
    assert token not in phone.page.content()


def test_e2e_as04_stolen_token_useless_after_logout(phone: Phone, api: Api) -> None:
    account = new_account()
    phone.signup(account)
    phone.deposit("20")
    stolen = phone.token()
    assert stolen

    phone.logout()

    assert api.get("/api/wallet", stolen).status_code == 401
    assert api.post("/api/auth/refresh", stolen).status_code == 401
    phone.plant_token(stolen)
    phone.open("#/wallet")
    expect(phone.page.get_by_role("heading", name="Connexion")).to_be_visible()
    assert phone.token() is None
    assert "Mon solde" not in phone.page.content()
    assert account.display_name not in phone.page.content()


def test_e2e_as01_forged_tokens_send_back_to_login(phone: Phone, api: Api) -> None:
    phone.signup(new_account())
    genuine = phone.token()
    assert genuine
    header, payload, signature = genuine.split(".")
    claims = _claims(genuine)
    forged = [
        f"{_b64({'alg': 'none', 'typ': 'JWT'})}.{payload}.",
        f"{header}.{payload}.{signature[::-1]}",
        f"{header}.{_b64({**claims, 'exp': 4_102_444_800})}.{signature}",
        f"{header}.{_b64({**claims, 'role': 'moderator'})}.{signature}",
    ]

    for token in forged:
        assert api.get("/api/me", token).status_code == 401
        phone.plant_token(token)
        phone.open("#/wallet")
        expect(phone.page.get_by_role("heading", name="Connexion")).to_be_visible()
        assert phone.token() is None
        assert "Mon solde" not in phone.page.content()

    assert api.get("/api/me", genuine).status_code == 200


def test_e2e_as12_role_and_kyc_fields_rejected_at_signup(phone: Phone) -> None:
    account = new_account()
    phone.page.route(
        "**/api/auth/register",
        tampered_json({"role": "moderator", "kyc_status": "verified"}),
        times=1,
    )

    response = phone.register(account)

    assert response.status == 422
    expect(phone.flash).to_have_text(INVALID_DATA)
    assert phone.submit_login(account.email, account.password).status == 401
    expect(phone.flash).to_have_text(BAD_CREDENTIALS)


def test_e2e_as05_html_display_name_rejected_at_signup(phone: Phone) -> None:
    account = new_account()
    phone.open("#/register")
    expect(phone.page.get_by_role("heading", name="Créer un compte")).to_be_visible()
    phone.page.locator("input[name=display_name]").evaluate(
        "input => input.removeAttribute('pattern')"
    )
    hostile = Account(account.email, "<img src=x onerror=alert(1)>")

    response = phone.register(hostile)

    assert response.status == 422
    expect(phone.flash).to_have_text(INVALID_DATA)
    assert "onerror" not in response.text()


def test_e2e_as12_player_cannot_open_moderation(phone: Phone) -> None:
    phone.signup(new_account())

    expect(phone.page.locator("#nav-moderation")).to_be_hidden()
    with phone.page.expect_response("**/api/moderation/disputes") as info:
        phone.open("#/moderation")

    assert info.value.status == 403
    expect(phone.flash).to_have_text(FORBIDDEN)
    expect(phone.page.get_by_role("heading", name="Litiges en attente")).to_have_count(0)


def test_e2e_as14_moderator_cannot_create_bet_or_join(
    phones: Callable[..., Phone], api: Api
) -> None:
    bookie = api.signup(new_account("bookie"))
    bet_id = api.create_bet(bookie)
    link = api.invitation_url(bookie, bet_id)
    moderator = new_account("mod")
    seed_account(api.stack, moderator, moderator=True)
    phone = phones()

    phone.login(moderator)
    phone.page.wait_for_url("**/#/moderation")
    expect(phone.page.get_by_role("heading", name="Litiges en attente")).to_be_visible()
    phone.fill_new_bet("Pari du modérateur")
    created = phone.submit_new_bet()
    with phone.page.expect_response("**/api/invitations/accept") as accepted:
        phone.follow_link(link)

    assert created.status == 403
    assert accepted.value.status == 403
    expect(phone.flash).to_have_text(FORBIDDEN)
    assert api.get(f"/api/bets/{bet_id}", api.login(moderator)).status_code == 404


def test_e2e_as01_login_flood_gets_429_before_password_check(strict_phone: Phone) -> None:
    account = new_account()
    seed_account(strict_phone.stack, account)
    statuses = [strict_phone.submit_login(account.email, "wrong password").status for _ in range(5)]
    expect(strict_phone.flash).to_have_text(BAD_CREDENTIALS)

    blocked = strict_phone.submit_login(account.email, account.password)

    assert statuses == [401] * 5
    assert blocked.status == 429
    assert int(blocked.headers["retry-after"]) > 0
    expect(strict_phone.flash).to_have_text(RATE_LIMITED)

    def spoof(route: Route) -> None:
        spoofed = f"203.0.113.{secrets.randbelow(250) + 1}"
        route.continue_(headers={**route.request.headers, "X-Forwarded-For": spoofed})

    strict_phone.page.route("**/api/auth/login", spoof)
    spoofed = [strict_phone.submit_login(account.email, account.password).status for _ in range(3)]
    assert spoofed == [429, 429, 429]
    assert strict_phone.token() is None
