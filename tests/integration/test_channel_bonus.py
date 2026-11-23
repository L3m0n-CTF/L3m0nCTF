"""The channel bonus on the real stack (nginx, gunicorn with four gevent workers, MariaDB, Redis): a studio that solves every programme of a channel is given a fixed
number of TRP once, by the end of the request that records the last flag.

The plugin tests (plugins/l3mon_scoring/tests/test_channel_bonus.py) prove the rules on SQLite, and the migration test proves the table and its key on a real MariaDB.
This proves the same behaviour end to end, over HTTP, where a hook that runs in the wrong order, a cache that is cleared too early, or a bonus that is not in the
public scoreboard would show: two studios, a channel of two programmes; the first flag pays its value only; the last flag pays its value and the bonus, once, and the reply
says so; the scoreboard and the studio's own awards show it; a Revoke takes the bonus back with the solve and a Restore returns the exact totals; the amount is the
crew's to change and refused to a player; a studio that completes the channel after the amount was changed is paid the new amount.

Same prerequisites and runner as test_stack.py. Like test_scoring.py it renders an event window around the present moment into the generated settings and restarts CTFd,
and puts everything back at the end; everything it creates is removed again (the cleanup also deletes the amount setting, which brings the default back).
"""
from __future__ import annotations

import json
import time
from types import SimpleNamespace

import pytest

import test_release as r

pytestmark = pytest.mark.integration

SCORING = "/api/v1/l3mon/admin/scoring"
ONE = {"name": "Quokka Alpha", "flag": "quokka-alpha-answer", "value": 100}
TWO = {"name": "Quokka Beta", "flag": "quokka-beta-answer", "value": 150}
PLAYERS = ["bonusplayer1", "bonusplayer2"]


def purge():
    for name in PLAYERS:
        for path, value in (("/api/v1/teams", f"{name}-team"), ("/api/v1/users", name)):
            status, out = r.admin("GET", f"{path}?view=admin&field=name&q={value}")
            for row in (out["data"] if status == 200 else []):
                if row["name"] == value:
                    r.admin("DELETE", f"{path}/{row['id']}")
    for spec in (ONE, TWO):
        status, out = r.admin("GET", f"/api/v1/challenges?view=admin&field=name&q={spec['name'].split()[0]}")
        for row in (out["data"] if status == 200 else []):
            if row["name"] == spec["name"]:
                r.admin("DELETE", f"/api/v1/challenges/{row['id']}")


@pytest.fixture(scope="module")
def world():
    original = r.PRESET.read_bytes()
    window = json.loads(original)
    now = int(time.time())
    window["start"], window["end"] = now - 3600, now + 7200
    r.PRESET.write_bytes((json.dumps(window, separators=(",", ":")) + "\n").encode("utf-8"))
    made = SimpleNamespace(players=[], teams=[], users=[])
    try:
        r.restart_ctfd()
        purge()
        made.one = r.make_challenge(ONE)
        made.two = r.make_challenge(TWO)
        status, out = r.admin("PUT", r.PROGRAMMES, {
            "channels": [{"slug": "bonus-street", "name": "Bonus Street", "position": 1}],
            "programmes": [
                {"challenge_id": made.one, "channel": "bonus-street", "cell": 0, "number": 101, "slug": "quokka_alpha"},
                {"challenge_id": made.two, "channel": "bonus-street", "cell": 1, "number": 102, "slug": "quokka_beta"},
            ],
        })
        assert status == 200, out
        status, view = r.admin("GET", r.RELEASE)
        channel = view["data"]["channels"][0]
        r.change({"kind": "channel", "id": channel["id"], "mode": "release"}, *[{"kind": "programme", "id": p["id"], "mode": "release"} for p in channel["programmes"]])
        for name in PLAYERS:
            session, user_id, team_id = r.make_player(name)
            made.players.append(session)
            made.users.append(user_id)
            made.teams.append(team_id)
        yield made
    finally:
        try:
            purge()
            r.in_ctfd(r.CLEANUP)
        finally:
            r.PRESET.write_bytes(original)
            r.restart_ctfd()


def attempt(session, cid, flag):
    status, out = r.post_json(session, "/api/v1/challenges/attempt", {"challenge_id": cid, "submission": flag})
    assert status == 200 and out["data"]["status"] == "correct", out
    return out["data"]["l3mon"]


def scores(session) -> dict:
    status, out = r.get_json(session, "/api/v1/scoreboard")
    assert status == 200, out
    return {row["name"]: row["score"] for row in out["data"]}


def crew(path: str, body=None, method="POST"):
    return r.admin(method, f"{SCORING}{path}", body)


def test_01_the_first_flag_pays_its_value_only_and_the_last_pays_the_bonus_once(world):
    first = attempt(world.players[0], world.one, ONE["flag"])
    assert first["channel_complete"] is False and first["channel_bonus"] == 0
    assert scores(world.players[0]) == {"bonusplayer1-team": 100}
    last = attempt(world.players[0], world.two, TWO["flag"])
    assert last["channel_complete"] is True and last["channel_bonus"] == 100, last
    assert scores(world.players[0]) == {"bonusplayer1-team": 100 + 150 + 100}, "both values and the bonus, in the public scoreboard at once"


def test_02_the_bonus_is_the_studios_own_award_with_an_empty_text_and_the_crews_list_does_not_show_it(world):
    status, awards = r.get_json(world.players[1], f"/api/v1/teams/{world.teams[0]}/awards")
    assert status == 200
    assert [(a["name"], a["value"], a["description"]) for a in awards["data"]] == [("Channel bonus +100 TRP", 100, "")], awards
    status, view = crew("", method="GET")
    assert status == 200
    assert view["data"]["bonuses"] == [] and view["data"]["channel_bonus"] == {"trp": 100, "default": 100, "max": 1000, "given": 1}, view["data"]["channel_bonus"]


def test_03_the_other_studio_with_one_flag_has_no_bonus(world):
    attempt(world.players[1], world.one, ONE["flag"])
    assert scores(world.players[1]) == {"bonusplayer1-team": 350, "bonusplayer2-team": 100}


def test_04_a_revoke_takes_the_bonus_back_with_the_solve_and_a_restore_returns_the_exact_totals(world):
    status, out = crew("/revoke", {"challenge_id": world.two, "reason": "The checker accepted a wrong answer"})
    assert status == 200, out
    assert scores(world.players[0]) == {"bonusplayer1-team": 100, "bonusplayer2-team": 100}, "the second solve and the bonus both came off"
    status, view = crew("", method="GET")
    assert view["data"]["channel_bonus"]["given"] == 0
    status, out = crew("/restore", {"challenge_id": world.two})
    assert status == 200, out
    assert scores(world.players[0]) == {"bonusplayer1-team": 350, "bonusplayer2-team": 100}
    status, view = crew("", method="GET")
    assert view["data"]["channel_bonus"]["given"] == 1


def test_05_the_amount_is_the_crews_to_change_and_a_new_amount_pays_the_next_studio(world):
    status, out = crew("/channel-bonus", {"trp": 250}, method="PUT")
    assert status == 200 and out["data"] == {"trp": 250, "was": 100}, out
    last = attempt(world.players[1], world.two, TWO["flag"])
    assert last["channel_complete"] is True and last["channel_bonus"] == 250, last
    assert scores(world.players[0]) == {"bonusplayer1-team": 350, "bonusplayer2-team": 100 + 150 + 250}, "the first studio keeps the 100 it was paid; the second gets 250"
    status, out = crew("/channel-bonus", {"trp": 5000}, method="PUT")
    assert status == 400, out


def put_as_player(session, path: str, body: dict):
    """post_json with PUT (test_release.py has only the POST one): a signed-in player's JSON write, with the CSRF token CTFd wants in a header."""
    import urllib.request

    token = session.nonce("/challenges")
    request = urllib.request.Request(
        f"http://127.0.0.1:{r.t.PORT}{path}", data=json.dumps(body).encode(), method="PUT",
        headers={"Host": r.t.HOST, "Content-Type": "application/json", "CSRF-Token": token},
    )
    try:
        response = session.opener.open(request, timeout=20)
    except Exception as error:  # an HTTP error status is a normal answer here
        response = error
    return response.status, response.read()[:200]


def test_06_a_player_cannot_set_the_amount(world):
    status, out = put_as_player(world.players[0], f"{SCORING}/channel-bonus", {"trp": 1})
    assert status in (302, 401, 403), (status, out)
    status, view = crew("", method="GET")
    assert view["data"]["channel_bonus"]["trp"] == 250
