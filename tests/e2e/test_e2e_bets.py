import json
import secrets
from collections.abc import Callable
from datetime import timedelta
from typing import Any

from browser_flows import (
    INVALID_DATA,
    NOT_FOUND,
    RATE_LIMITED,
    Api,
    Phone,
    expire_invitation,
    new_account,
    seed_account,
    start_event,
    tampered_json,
)
from playwright.sync_api import Response, expect

TITLE_XSS = "<img src=x onerror=alert('titre')>Matis<script>alert(1)</script>"
DESCRIPTION_XSS = "<svg onload=alert('description')></svg><iframe src=javascript:alert(2)>"
NOTE_XSS = "<img src=x onerror=alert('note')> voir https://evidence.example/preuve.png"
REASON_XSS = "<script>alert('motif')</script> le bookie ment"
JUSTIFICATION_XSS = "<img src=x onerror=alert('modo')> vidéo vérifiée, OUI est correct"
INJECTED = "#view img, #view script, #view svg, #view iframe"
BET_TAMPERING: list[dict[str, Any]] = [
    {"platform_commission_bps": 0},
    {"bookie_commission_bps": 10_000},
    {"bookie_commission_bps": -1},
    {"bookie_commission_bps": "400"},
    {"bookie_commission_bps": 4.5},
    {"status": "settled"},
    {"bookie_id": "00000000-0000-4000-8000-000000000000"},
    {"min_stake_cents": -100},
    {"min_stake_cents": 150.5},
    {"final_outcome": "OUI"},
]


def _assert_rendered_as_text(phone: Phone, *payloads: str) -> None:
    for payload in payloads:
        expect(phone.view.get_by_text(payload, exact=False).first).to_be_visible()
    expect(phone.page.locator(INJECTED)).to_have_count(0)
    assert phone.dialogs == []
    assert phone.csp_violations == []


def test_e2e_as05_no_csp_violation_on_any_page(phones: Callable[..., Phone], api: Api) -> None:
    phone = phones()
    documents: list[Response] = []

    def keep_documents(response: Response) -> None:
        if response.request.is_navigation_request():
            documents.append(response)

    phone.page.on("response", keep_documents)
    phone.open("#/login")
    phone.open("#/register")
    account = new_account()
    phone.signup(account)
    phone.open("#/bets")
    phone.open("#/bets/new")
    bet_id = phone.create_bet("Matis va-t-il réussir son créneau ?")
    phone.invite(bet_id)
    phone.deposit("20")
    phone.verify_identity()
    moderator_account = new_account("mod")
    seed_account(api.stack, moderator_account, moderator=True)
    moderator = phones()
    moderator.login(moderator_account)
    expect(moderator.page.get_by_role("heading", name="Litiges en attente")).to_be_visible()

    assert phone.csp_violations == []
    assert moderator.csp_violations == []
    assert len(documents) >= 4
    for document in documents:
        policy = document.headers["content-security-policy"]
        assert "script-src 'self'" in policy
        assert "unsafe-inline" not in policy
        assert "unsafe-eval" not in policy


def test_e2e_as05_tampered_bet_parameters_rejected(phone: Phone) -> None:
    phone.signup(new_account("bookie"))
    phone.fill_new_bet("Matis va-t-il réussir son créneau ?")

    for changes in BET_TAMPERING:
        phone.page.route("**/api/bets", tampered_json(changes), times=1)
        assert phone.submit_new_bet().status == 422, changes
        expect(phone.flash).to_have_text(INVALID_DATA)
    phone.fill_new_bet(
        "Clôture après le début", closes_in=timedelta(hours=3), starts_in=timedelta(hours=2)
    )
    assert phone.submit_new_bet().status == 422
    phone.fill_new_bet("Clôture trop proche", closes_in=timedelta(minutes=2))
    assert phone.submit_new_bet().status == 422
    phone.fill_new_bet("Événement trop lointain", starts_in=timedelta(days=120))
    assert phone.submit_new_bet().status == 422

    phone.open("#/bets")
    expect(phone.view.get_by_text("Aucun pari pour l'instant.", exact=False)).to_be_visible()


def test_e2e_as13_oversized_body_gets_413(phone: Phone) -> None:
    phone.signup(new_account("bookie"))
    phone.fill_new_bet("Matis va-t-il réussir son créneau ?")
    phone.page.route("**/api/bets", tampered_json({"description": "A" * 70_000}), times=1)

    response = phone.submit_new_bet()

    assert response.status == 413
    expect(phone.flash).to_have_text("Requête trop volumineuse.")


def test_e2e_as06_used_invitation_link_cannot_be_replayed(
    phones: Callable[..., Phone], api: Api
) -> None:
    bookie = api.signup(new_account("bookie"))
    bet_id = api.create_bet(bookie)
    link = api.invitation_url(bookie, bet_id)
    first, second = phones(), phones()
    first.signup(new_account("first"))
    second.signup(new_account("second"))

    assert first.join(link) == bet_id
    with second.page.expect_response("**/api/invitations/accept") as replay:
        second.follow_link(link)

    assert replay.value.status == 404
    expect(second.flash).to_have_text(NOT_FOUND)
    second.open(f"#/bets/{bet_id}")
    expect(second.flash).to_have_text(NOT_FOUND)
    assert "Matis va-t-il" not in second.page.content()
    assert len(api.bet(bookie, bet_id)["participants"]) == 1


def test_e2e_as06_expired_and_guessed_invitation_links_refused(phone: Phone, api: Api) -> None:
    bookie = api.signup(new_account("bookie"))
    bet_id = api.create_bet(bookie)
    link = api.invitation_url(bookie, bet_id)
    expire_invitation(api.stack, link)
    guessed = f"{link.rsplit('/', 1)[0]}/{secrets.token_urlsafe(32)}"
    phone.signup(new_account())

    for url in (link, guessed):
        with phone.page.expect_response("**/api/invitations/accept") as accepted:
            phone.follow_link(url)
        assert accepted.value.status == 404
        expect(phone.flash).to_have_text(NOT_FOUND)

    assert api.bet(bookie, bet_id)["participants"] == []
    phone.open("#/bets")
    expect(phone.view.get_by_text("Aucun pari pour l'instant.", exact=False)).to_be_visible()


def test_e2e_as09_only_the_bookie_proposes_and_only_once(
    phones: Callable[..., Phone], api: Api
) -> None:
    bookie_account = new_account("bookie")
    bookie_token = api.signup(bookie_account)
    bet_id = api.create_bet(bookie_token)
    bettor_account = new_account("bettor")
    bettor_token = api.signup(bettor_account)
    api.fund(bettor_token, 2000)
    api.join(bettor_token, api.invitation_url(bookie_token, bet_id))
    assert api.stake(bettor_token, bet_id, "OUI", 1000).status_code == 201
    start_event(api.stack, bet_id)
    bettor = phones()
    bettor.login(bettor_account)
    bettor.open(f"#/bets/{bet_id}")
    expect(bettor.view.get_by_text("Clos", exact=True)).to_be_visible()

    expect(bettor.page.get_by_role("button", name="Proposer le résultat")).to_have_count(0)
    forged = bettor.fetch("POST", f"/api/bets/{bet_id}/result", {"outcome": "OUI"})
    bookie = phones()
    bookie.login(bookie_account)
    bookie.open(f"#/bets/{bet_id}")
    bookie.page.get_by_label("Résultat").select_option("NON")
    bookie.page.get_by_role("button", name="Proposer le résultat").click()
    expect(bookie.view.get_by_text("Résultat proposé").first).to_be_visible()
    second = bookie.fetch("POST", f"/api/bets/{bet_id}/result", {"outcome": "OUI"})

    assert forged["status"] == 403
    assert second["status"] == 409
    assert api.bet(bookie_token, bet_id)["proposed_outcome"] == "NON"


def test_e2e_as10_contest_needs_a_stake_and_happens_once(
    phones: Callable[..., Phone], api: Api
) -> None:
    bookie = api.signup(new_account("bookie"))
    bet_id = api.create_bet(bookie)
    staker_account, idle_account = new_account("staker"), new_account("idle")
    staker_token, idle_token = api.signup(staker_account), api.signup(idle_account)
    api.fund(staker_token, 2000)
    for token in (staker_token, idle_token):
        api.join(token, api.invitation_url(bookie, bet_id))
    assert api.stake(staker_token, bet_id, "OUI", 1000).status_code == 201
    start_event(api.stack, bet_id)
    assert api.post(f"/api/bets/{bet_id}/result", bookie, {"outcome": "NON"}).status_code == 200
    idle, staker = phones(), phones()
    idle.login(idle_account)
    staker.login(staker_account)

    idle.open(f"#/bets/{bet_id}")
    expect(idle.page.get_by_role("button", name="Contester le résultat")).to_have_count(0)
    idle_contest = idle.fetch(
        "POST", f"/api/bets/{bet_id}/contests", {"reason": "Je n'ai rien misé mais je conteste."}
    )
    staker.open(f"#/bets/{bet_id}")
    staker.page.get_by_label("Motif de contestation").fill("          ")
    with staker.page.expect_response(f"**/api/bets/{bet_id}/contests") as blank:
        staker.page.get_by_role("button", name="Contester le résultat").click()
    expect(staker.flash).to_have_text(INVALID_DATA)
    staker.page.get_by_label("Motif de contestation").fill("Le bookie a menti sur le résultat.")
    staker.page.get_by_role("button", name="Contester le résultat").click()
    expect(staker.view.get_by_text("En litige")).to_be_visible()
    again = staker.fetch("POST", f"/api/bets/{bet_id}/contests", {"reason": "Encore une fois !!!"})

    assert idle_contest["status"] == 403
    assert blank.value.status == 422
    assert again["status"] == 409


def test_e2e_as16_errors_and_paths_leak_nothing(phone: Phone, api: Api) -> None:
    phone.signup(new_account("bookie"))
    phone.fill_new_bet("Matis va-t-il réussir son créneau ?")
    phone.page.route(
        "**/api/bets",
        lambda route: route.continue_(post_data='{"title": "<script>secret</script>", "bookie'),
        times=1,
    )
    broken = phone.submit_new_bet()
    phone.page.route(
        "**/api/bets", tampered_json({"title": "' OR 1=1 --<b>leak</b>", "closes_at": "x"}), times=1
    )
    invalid = phone.submit_new_bet()
    injected = phone.fetch("GET", "/api/bets?status=open'%20OR%201=1--")
    paths = {
        path: api.client.get(path)
        for path in (
            "/docs",
            "/redoc",
            "/openapi.json",
            "/..%2f..%2fpyproject.toml",
            "/js/..%2f..%2fconfig.py",
            "/%2e%2e/%2e%2e/etc/passwd",
            "/static/../../../etc/passwd",
        )
    }

    for response_text in (broken.text(), invalid.text(), injected["text"]):
        assert "Traceback" not in response_text
        assert "sqlalchemy" not in response_text.lower()
        assert 'File "' not in response_text
        assert "secret" not in response_text
        assert "leak" not in response_text
    assert broken.status == 422
    assert invalid.status == 422
    assert injected["status"] == 422
    expect(phone.flash).to_have_text(INVALID_DATA)
    for path, response in paths.items():
        assert response.status_code == 404, path
        assert "server" not in response.headers
        assert "root:" not in response.text
    assert json.loads(broken.text())["error"]["request_id"]


def test_e2e_as06_invitation_brute_force_gets_429(strict_phone: Phone) -> None:
    account = new_account()
    seed_account(strict_phone.stack, account)
    strict_phone.login(account)

    attempts = [
        strict_phone.fetch("POST", "/api/invitations/accept", {"token": secrets.token_urlsafe(32)})
        for _ in range(11)
    ]

    assert [attempt["status"] for attempt in attempts] == [404] * 10 + [429]
    assert int(attempts[-1]["headers"]["retry-after"]) > 0


def test_e2e_as13_write_flood_gets_429(strict_phone: Phone) -> None:
    account = new_account("bookie")
    seed_account(strict_phone.stack, account)
    strict_phone.login(account)
    bet_id = strict_phone.create_bet("Matis va-t-il réussir son créneau ?")
    invite = strict_phone.page.get_by_role("button", name="Créer un lien d'invitation")
    statuses: list[int] = []

    while not statuses or statuses[-1] != 429:
        with strict_phone.page.expect_response(f"**/api/bets/{bet_id}/invitations") as info:
            invite.click()
        statuses.append(info.value.status)
        assert len(statuses) <= 25

    assert statuses == [201] * 19 + [429]
    assert int(info.value.headers["retry-after"]) > 0
    expect(strict_phone.flash).to_have_text(RATE_LIMITED)


def test_e2e_as10_signup_flood_gets_429(strict_phone: Phone) -> None:
    statuses = [strict_phone.register(new_account()).status for _ in range(6)]

    assert statuses == [202] * 5 + [429]
    expect(strict_phone.flash).to_have_text(RATE_LIMITED)
