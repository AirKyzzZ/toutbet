import json
import uuid

from fastapi.testclient import TestClient

from support import Api, Player, idempotency_headers, staked_bet


def test_as03_other_user_deposit_returns_404(api: Api, client: TestClient) -> None:
    alice = api.signup("alice")
    bob = api.signup("bob")
    deposit_id = api.start_deposit(alice, 2000).json()["deposit_id"]

    other = client.get(f"/api/deposits/{deposit_id}", headers=bob.headers)
    unknown = client.get(f"/api/deposits/{uuid.uuid4()}", headers=bob.headers)

    assert other.status_code == unknown.status_code == 404
    assert other.json()["error"]["code"] == unknown.json()["error"]["code"] == "NOT_FOUND"
    assert other.json()["error"]["message"] == unknown.json()["error"]["message"]
    assert "2000" not in other.text


def test_as03_object_access_matrix(api: Api, client: TestClient) -> None:
    scenario = staked_bet(api, [("bob", "OUI", 1000)])
    alice, bob = scenario.bookie, scenario.players["bob"]
    carol = api.signup("carol")
    moderator = api.moderator()
    api.fund(carol, 1000)
    bet_id = scenario.bet["id"]
    deposit_id = api.start_deposit(alice, 1000).json()["deposit_id"]

    def status(method: str, path: str, player: Player, body: object = None) -> int:
        headers = idempotency_headers(player)
        return client.request(method, path, headers=headers, json=body).status_code

    stake_body = {"outcome": "NON", "amount_cents": 500}
    matrix = [
        ("GET", f"/api/bets/{bet_id}", None, {alice: 200, bob: 200, carol: 404, moderator: 404}),
        ("GET", f"/api/deposits/{deposit_id}", None, {alice: 200, bob: 404, carol: 404}),
        ("POST", f"/api/bets/{bet_id}/stakes", stake_body, {carol: 404}),
        ("POST", f"/api/bets/{bet_id}/invitations", None, {carol: 404, bob: 403}),
        ("POST", f"/api/bets/{bet_id}/cancel", None, {carol: 404, bob: 403}),
        ("POST", f"/api/bets/{bet_id}/result", {"outcome": "OUI"}, {carol: 404, bob: 403}),
        ("POST", f"/api/bets/{bet_id}/contests", {"reason": "x" * 20}, {carol: 404}),
    ]
    for method, path, body, expected in matrix:
        for player, code in expected.items():
            assert status(method, path, player, body) == code, (method, path, player.display_name)
    assert api.balance(carol) == 1000
    assert api.bet(alice, bet_id)["status"] == "open"


def test_as03_bet_view_shows_only_pseudonyms_and_totals(api: Api, client: TestClient) -> None:
    scenario = staked_bet(api, [("bob", "OUI", 1000), ("carol", "NON", 2500)])
    bob = scenario.players["bob"]

    seen_by_bob = api.bet(bob, scenario.bet["id"])
    seen_by_bookie = api.bet(scenario.bookie, scenario.bet["id"])

    assert seen_by_bob["participants"] == ["bob", "carol"]
    assert seen_by_bob["totals"] == {"OUI": 1000, "NON": 2500}
    assert seen_by_bob["my_stake"]["amount_cents"] == 1000
    assert seen_by_bookie["my_stake"] is None
    serialized = json.dumps(seen_by_bob)
    assert "@" not in serialized
    assert scenario.players["carol"].id not in serialized
    assert "2500" not in json.dumps(seen_by_bob["my_stake"])
    listing = client.get("/api/bets", headers=bob.headers).json()["items"][0]
    assert listing["my_stake"]["amount_cents"] == 1000


def test_as03_responses_never_contain_password_hash(api: Api, client: TestClient) -> None:
    scenario = staked_bet(api, [("bob", "OUI", 1000)])
    bob = scenario.players["bob"]
    bet_id = scenario.bet["id"]

    bodies = [
        client.get("/api/me", headers=bob.headers).text,
        client.get("/api/wallet", headers=bob.headers).text,
        client.get("/api/bets", headers=bob.headers).text,
        client.get(f"/api/bets/{bet_id}", headers=bob.headers).text,
        api.register(email=bob.email, display_name="bob_again").text,
    ]

    for body in bodies:
        assert "argon2" not in body
        assert "password" not in body
        assert "kyc_status" not in body or body == bodies[0]
        assert scenario.bookie.email not in body
