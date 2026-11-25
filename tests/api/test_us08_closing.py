from datetime import datetime, timedelta

from support import Api, staked_bet


def test_us08_1_worker_closes_bet_when_deadline_reached(api: Api) -> None:
    scenario = staked_bet(api, [("bob", "OUI", 1000)])
    closes_at = datetime.fromisoformat(scenario.bet["closes_at"])
    api.clock.advance(closes_at - api.clock.now() - timedelta(seconds=1))
    api.relogin(*scenario.everyone)

    before = api.tick()
    api.clock.advance(timedelta(seconds=1))
    after = api.tick()

    assert before.closed == 0
    assert after.closed == 1
    assert api.bet(scenario.bookie, scenario.bet["id"])["status"] == "closed"


def test_us08_2_each_stake_checks_server_clock_without_worker(api: Api) -> None:
    scenario = staked_bet(api, [])
    player = api.signup()
    api.fund(player, 2000)
    api.add_participants(scenario.bookie, scenario.bet["id"], player)
    closes_at = datetime.fromisoformat(scenario.bet["closes_at"])
    api.clock.advance(closes_at - api.clock.now() - timedelta(seconds=1))
    api.relogin(player)

    accepted = api.stake(player, scenario.bet["id"], "OUI", 500)
    api.clock.advance(timedelta(seconds=1))
    late = api.stake(player, scenario.bet["id"], "NON", 500)

    assert accepted.status_code == 201
    assert late.status_code == 409
    assert late.json()["error"]["code"] == "BET_CLOSED"


def test_us08_3_bet_without_result_72h_after_start_is_disputed(api: Api) -> None:
    scenario = staked_bet(api, [("bob", "OUI", 1000)])
    api.start_event(scenario.bet, *scenario.everyone)
    starts = datetime.fromisoformat(scenario.bet["event_starts_at"])

    api.clock.set(starts + timedelta(hours=72) - timedelta(seconds=1))
    early = api.tick()
    api.clock.set(starts + timedelta(hours=72))
    late = api.tick()
    api.relogin(*scenario.everyone)

    assert early.escalated == 0
    assert late.escalated == 1
    assert api.bet(scenario.bookie, scenario.bet["id"])["status"] == "disputed"
