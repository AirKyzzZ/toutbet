from collections.abc import Callable

from browser_flows import Api, Phone, new_account
from playwright.sync_api import expect


def test_e2e_critical_journey_signup_deposit_bet_stake_logout(
    phones: Callable[..., Phone], api: Api
) -> None:
    bookie_phone = phones()
    bookie = new_account("bookie")
    bookie_phone.signup(bookie)
    bookie_phone.deposit("20")
    bookie_phone.expect_balance(2000)
    bookie_phone.verify_identity()
    bet_id = bookie_phone.create_bet("Matis va-t-il réussir son créneau ?")
    link = bookie_phone.invite(bet_id)

    bettor_phone = phones()
    bettor = new_account("bettor")
    bettor_phone.follow_link(link)
    expect(bettor_phone.page.get_by_role("heading", name="Connexion")).to_be_visible()
    bettor_phone.page.get_by_role("link", name="Créer un compte").click()
    assert bettor_phone.register(bettor).status == 202
    bettor_phone.login(bettor)
    bettor_phone.page.wait_for_url(f"**/#/bets/{bet_id}")
    expect(bettor_phone.page.get_by_role("heading", name="Matis va-t-il")).to_be_visible()
    bettor_phone.deposit("20")
    staked = bettor_phone.stake(bet_id, "OUI", "10")
    assert staked.status == 201
    expect(bettor_phone.view.get_by_text("OUI · 10,00 €")).to_be_visible()
    bettor_phone.expect_balance(1000)

    bookie_phone.open(f"#/bets/{bet_id}")
    expect(bookie_phone.view.get_by_text(bettor.display_name)).to_be_visible()

    tokens = [bookie_phone.token(), bettor_phone.token()]
    assert all(tokens)
    for device in (bookie_phone, bettor_phone):
        device.logout()
        assert device.token() is None
    for token in tokens:
        assert token is not None
        assert api.get("/api/wallet", token).status_code == 401
        assert api.get("/api/me", token).status_code == 401
        assert api.post("/api/auth/refresh", token).status_code == 401

    for device, account in ((bookie_phone, bookie), (bettor_phone, bettor)):
        for route in ("#/wallet", f"#/bets/{bet_id}", "#/bets"):
            device.open(route)
            expect(device.page.get_by_role("heading", name="Connexion")).to_be_visible()
        device.page.go_back()
        device.page.go_back()
        expect(device.page.get_by_role("heading", name="Connexion")).to_be_visible()
        content = device.page.content()
        for private in ("Mon solde", account.display_name, account.email, "Matis va-t-il"):
            assert private not in content
