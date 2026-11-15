"""Races on the real stack: players who act in the same instant (found by the end-to-end test of 15 November).

CTFd checks a limit and writes afterwards, with nothing between the two; on SQLite (the plugin tests) one writer runs at a time and the
race cannot happen, so it can only be proved here, on MariaDB behind nginx and four gevent workers:

- JOIN: eight players press Join on the same studio at the same moment; the event's team size is 4, so exactly three get in
  (the captain is the fourth) and five are told the studio is full. Before the studio's row was locked, the studio ended with more.
- UNLOCK: a studio with 100 TRP asks for three different hints that cost 60 each, from three members at the same moment; exactly one
  is paid for and the score ends at 40. Four members then ask for one and the same hint: it is unlocked and charged once.
  Before the lock the studio's score went below nothing.

Same prerequisites and runner as test_stack.py. Like test_scoring.py it renders an event window around the present moment into the
generated settings and restarts CTFd, and puts everything back at the end; everything it creates is removed again.
"""
from __future__ import annotations

import json
import secrets as pysecrets
import threading
import urllib.request
from types import SimpleNamespace

import pytest

import test_release as r
import test_stack as t

pytestmark = pytest.mark.integration

STUDIO = "racestudio"
CAPTAIN = "racecaptain"
JOINERS = [f"racejoiner{i}" for i in range(1, 9)]
CHALLENGE = {"name": "Race Wombat", "flag": "race-wombat-answer", "value": 100}
COST = 60


def purge():
    for path, names in (("/api/v1/users", [CAPTAIN, *JOINERS]), ("/api/v1/teams", [STUDIO])):
        for name in names:
            status, out = r.admin("GET", f"{path}?view=admin&field=name&q={name}")
            for row in (out["data"] if status == 200 else []):
                if row["name"] == name:
                    r.admin("DELETE", f"{path}/{row['id']}")
    status, out = r.admin("GET", f"/api/v1/challenges?view=admin&field=name&q={CHALLENGE['name'].split()[0]}")
    for row in (out["data"] if status == 200 else []):
        if row["name"] == CHALLENGE["name"]:
            r.admin("DELETE", f"/api/v1/challenges/{row['id']}")


def make_user(name: str):
    """A verified player with no studio, signed in. Returns (session, user_id)."""
    password = pysecrets.token_urlsafe(18)
    status, out = r.admin("POST", "/api/v1/users", {"name": name, "email": f"{name}@example.com", "password": password, "type": "user", "verified": True})
    assert status == 200, out
    session = t.Session()
    answer = session.open("/login", {"name": name, "password": password, "_submit": "Submit", "nonce": session.nonce("/login")})
    assert answer.status == 302, f"sign-in answered {answer.status}"
    return session, out["data"]["id"]


def together(jobs):
    """Run every job at the same moment (each thread waits at a gate until all are ready) and return the results in the order given."""
    gate = threading.Barrier(len(jobs))
    results = [None] * len(jobs)

    def run(index, job):
        gate.wait()
        try:
            results[index] = job()
        except Exception as error:  # a failed request is a result too
            results[index] = error

    threads = [threading.Thread(target=run, args=(i, job)) for i, job in enumerate(jobs)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(60)
    return results


def post_json_with(session: t.Session, path: str, body: dict, token: str):
    """A JSON write with a CSRF token fetched beforehand (so that the writes of many players leave at the same moment)."""
    request = urllib.request.Request(
        f"http://127.0.0.1:{t.PORT}{path}", data=json.dumps(body).encode(), method="POST",
        headers={"Host": t.HOST, "Content-Type": "application/json", "CSRF-Token": token},
    )
    try:
        response = session.opener.open(request, timeout=30)
    except Exception as error:  # an HTTP error status is a normal answer here
        response = error
    raw = response.read()
    try:
        return response.status, json.loads(raw)
    except ValueError:
        return response.status, raw.decode("utf-8", "replace")


def members_of(team_id: int) -> int:
    status, out = r.admin("GET", f"/api/v1/teams/{team_id}/members")
    assert status == 200, out
    return len(out["data"])


def score_of(team_id: int) -> int:
    status, out = r.admin("GET", f"/api/v1/teams/{team_id}")
    assert status == 200, out
    return out["data"]["score"]


@pytest.fixture(scope="module")
def world():
    import time

    original = r.PRESET.read_bytes()
    window = json.loads(original)
    now = int(time.time())
    window["start"], window["end"] = now - 3600, now + 7200
    r.PRESET.write_bytes((json.dumps(window, separators=(",", ":")) + "\n").encode("utf-8"))
    made = SimpleNamespace()
    try:
        r.restart_ctfd()
        purge()
        r.in_ctfd(r.CLEANUP)  # no plan from an earlier run: with none, the challenge below is visible the plain way
        captain, captain_id = make_user(CAPTAIN)
        studio_password = pysecrets.token_urlsafe(12)
        answer = captain.open("/teams/new", {"name": STUDIO, "password": studio_password, "_submit": "Create", "nonce": captain.nonce("/teams/new")})
        assert answer.status == 302, f"studio creation answered {answer.status}: {answer.read()[:300]!r}"
        status, who = r.admin("GET", f"/api/v1/users/{captain_id}")
        made.team_id, made.captain, made.captain_id, made.password = who["data"]["team_id"], captain, captain_id, studio_password
        made.joiners = [make_user(name) for name in JOINERS]
        yield made
    finally:
        try:
            purge()
            r.in_ctfd(r.CLEANUP)
        finally:
            r.PRESET.write_bytes(original)
            r.restart_ctfd()


def test_01_eight_players_join_one_studio_at_the_same_moment_and_only_the_places_left_are_filled(world):
    assert members_of(world.team_id) == 1
    ready = [(session, session.nonce("/teams/join")) for session, _ in world.joiners]
    jobs = [
        (lambda s=session, n=nonce: s.open("/teams/join", {"name": STUDIO, "password": world.password, "_submit": "Join", "nonce": n}))
        for session, nonce in ready
    ]
    answers = together(jobs)
    statuses = [a.status if hasattr(a, "status") else repr(a) for a in answers]
    assert members_of(world.team_id) == 4, f"the studio ended with {members_of(world.team_id)} members (answers: {statuses})"
    assert sorted(statuses).count(302) == 3, statuses  # three went in, the others got the page that says the studio is full
    assert all(s in (302, 200) for s in statuses), statuses


def test_02_a_studio_cannot_spend_more_than_it_has_when_its_members_unlock_hints_together(world):
    # the studio's four members: the captain and the first three joiners that got in
    status, out = r.admin("GET", f"/api/v1/teams/{world.team_id}/members")
    member_ids = set(out["data"])
    sessions = [world.captain] + [session for session, user_id in world.joiners if user_id in member_ids]
    assert len(sessions) == 4, "the join test above leaves the studio with four members"

    status, out = r.admin("POST", "/api/v1/challenges", {"name": CHALLENGE["name"], "category": "Test", "description": "plain words", "value": CHALLENGE["value"], "state": "visible", "type": "standard"})
    assert status == 200, out
    cid = out["data"]["id"]
    status, _ = r.admin("POST", "/api/v1/flags", {"challenge_id": cid, "content": CHALLENGE["flag"], "type": "static", "data": ""})
    assert status == 200
    hints = []
    for number in range(3):
        status, out = r.admin("POST", "/api/v1/hints", {"challenge_id": cid, "content": f"clue {number}", "cost": COST})
        assert status == 200, out
        hints.append(out["data"]["id"])
    status, out = r.admin("POST", "/api/v1/hints", {"challenge_id": cid, "content": "small clue", "cost": 10})
    assert status == 200, out
    small = out["data"]["id"]
    status, out = r.admin("POST", "/api/v1/awards", {"user_id": world.captain_id, "team_id": world.team_id, "name": "race money", "value": 100, "category": "bonus", "description": "test"})
    assert status == 200, out
    assert score_of(world.team_id) == 100

    # three different hints at 60 each, three members at once: one is affordable (100 -> 40), the other two must be refused
    tokens = [s.nonce("/challenges") for s in sessions[:3]]
    jobs = [(lambda s=s, h=h, tk=tk: post_json_with(s, "/api/v1/unlocks", {"target": h, "type": "hints"}, tk)) for s, h, tk in zip(sessions[:3], hints, tokens)]
    answers = together(jobs)
    codes = sorted(a[0] if isinstance(a, tuple) else repr(a) for a in answers)
    assert codes == [200, 400, 400], answers
    assert score_of(world.team_id) == 40, "the studio was charged for more than it could pay"

    # one and the same 10-TRP hint, four members at once: unlocked once, charged once
    tokens = [s.nonce("/challenges") for s in sessions]
    jobs = [(lambda s=s, tk=tk: post_json_with(s, "/api/v1/unlocks", {"target": small, "type": "hints"}, tk)) for s, tk in zip(sessions, tokens)]
    answers = together(jobs)
    codes = sorted(a[0] if isinstance(a, tuple) else repr(a) for a in answers)
    assert codes == [200, 400, 400, 400], answers
    assert score_of(world.team_id) == 30, "the same hint was charged more than once"
