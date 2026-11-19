"""The crew-only switch (docs/runbooks/15-rehearsal.md) on the real nginx.

While a rehearsal is on, nginx shuts every visitor out of the pages and routes that carry the programmes, the scores and the flags, except on the crew
host name and from the organisers' addresses; the player pages answer a plain "not on air yet" page and the API a short JSON answer, the public CTFtime feed
reads an empty list, and registration, sign-in and the health routes are not touched. The switch is one file that nginx reads on a reload.

Same prerequisites and runner as test_stack.py. nginx is restarted with a temporary switch file (the repository's own file is never changed) and with
nobody counting as an organiser, then restarted again as it was.
"""
from __future__ import annotations

import json

import pytest

import test_hardening as h
import test_stack as t

pytestmark = pytest.mark.integration

SLATE = b"Not on air yet"
PAGES = ("/board", "/board/anything", "/guide", "/scoreboard", "/challenges", "/files/00000000/x.txt", "/teams/1", "/teams/123/", "/stories", "/story/anything")
API_GET = ("/api/v1/challenges", "/api/v1/challenges/1", "/api/v1/l3mon/board", "/api/v1/l3mon/guide", "/api/v1/l3mon/scoreboard", "/api/v1/l3mon/instances",
           "/api/v1/l3mon/story", "/api/v1/l3mon/story/anything", "/api/v1/l3mon/story/anything/pic/1.png",
           "/api/v1/scoreboard", "/api/v1/scoreboard/top/10", "/api/v1/hints/1", "/api/v1/users/me/solves", "/api/v1/teams/me/solves", "/api/v1/teams/3/awards")
# what must keep working while the gate is closed: registration (every step), sign-in, the player's own studio and settings, the health routes
NOT_TOUCHED = ("/login", "/register", "/reset_password", "/healthcheck", "/l3mon/healthz", "/rules", "/favicon.ico",
               "/teams/new", "/teams/join", "/teams/invite", "/team", "/settings", "/register/studio", "/api/v1/l3mon/teams/available", "/api/v1/l3mon/ticks")


@pytest.fixture(scope="module")
def switch(tmp_path_factory):
    """nginx with a switch file of its own, nobody an organiser, and the crew host name served as in production. Returns a function that sets the switch."""
    path = tmp_path_factory.mktemp("switch") / "crew-only.conf"
    path.write_text("default 0;\n", encoding="utf-8")
    t.compose("up", "-d", "--wait", "--force-recreate", "--no-deps", "nginx", env={"ADMIN_NETS_FILE": str(h.FIXTURE), "EXTRA_HOST": h.CREW_HOST, "CREW_ONLY_FILE": str(path)})
    t.wait_healthy("nginx", 60)

    def set_switch(on: bool):
        path.write_text("default 1;\n" if on else "default 0;\n", encoding="utf-8")  # written in place: the container sees the same file
        t.compose("exec", "-T", "nginx", "nginx", "-s", "reload")
        import time

        time.sleep(1.5)

    yield set_switch
    t.compose("up", "-d", "--wait", "--force-recreate", "--no-deps", "nginx")
    t.wait_healthy("nginx", 60)


def is_slate(body: bytes) -> bool:
    return SLATE in body


def answered_by_the_switch(headers) -> bool:
    return t.header_values(headers, "X-L3mon-Gate") == ["crew-only"]


def test_with_the_switch_off_nothing_is_changed(switch):
    switch(False)
    for path in PAGES + NOT_TOUCHED + API_GET:
        status, headers, body = t.request(path)
        assert not is_slate(body) and not answered_by_the_switch(headers) and b"not_live" not in body, (path, status)


def test_with_the_switch_on_the_player_pages_say_not_on_air_yet(switch):
    switch(True)
    for path in PAGES:
        status, headers, body = t.request(path)
        assert status == 200 and is_slate(body) and answered_by_the_switch(headers), (path, status, body[:80])
        assert t.header_values(headers, "Cache-Control") == ["no-store"], path
        # not a dead end: the player can still reach the studio, the settings and the sign-in page (none of which is gated)
        for way in (b'href="/team"', b'href="/settings"', b'href="/login"'):
            assert way in body, (path, way)


def test_with_the_switch_on_the_api_answers_a_short_json_refusal(switch):
    switch(True)
    for path in API_GET:
        status, headers, body = t.request(path, headers={"Accept": "application/json"})
        assert status == 403 and answered_by_the_switch(headers), (path, status)
        assert json.loads(body) == {"success": False, "error": "not_live", "message": "The broadcast has not started yet."}, (path, body[:100])
    # a flag cannot be sent either, and nothing reached the application
    status, headers, body = t.request("/api/v1/challenges/attempt", method="POST", headers={"Content-Type": "application/json"}, body=b'{"challenge_id": 1, "submission": "x"}')
    assert status == 403 and b"not_live" in body and answered_by_the_switch(headers)
    status, headers, body = t.request("/api/v1/unlocks", method="POST", headers={"Content-Type": "application/json"}, body=b'{"target": 1, "type": "hints"}')
    assert status == 403 and b"not_live" in body and answered_by_the_switch(headers)


def test_with_the_switch_on_registration_sign_in_and_the_health_routes_still_work(switch):
    switch(True)
    for path in NOT_TOUCHED:
        status, headers, body = t.request(path)
        assert not is_slate(body) and not answered_by_the_switch(headers), (path, status)
    status, _, body = t.request("/login")
    assert status == 200 and b"Sign in" in body


def test_with_the_switch_on_the_public_feed_reads_an_empty_list(switch):
    switch(True)
    status, headers, body = t.request("/ctftime/standings.json")
    assert status == 200 and json.loads(body) == {"standings": []} and answered_by_the_switch(headers)
    assert "json" in t.header_values(headers, "Content-Type")[0]
    switch(False)
    status, headers, body = t.request("/ctftime/standings.json")
    assert not answered_by_the_switch(headers), "with the switch off the platform's own feed is served again"


def test_with_the_switch_on_the_crew_host_still_reaches_everything(switch):
    switch(True)
    for path in PAGES[:5] + ("/api/v1/l3mon/board", "/ctftime/standings.json"):
        status, headers, body = t.request(path, host=h.CREW_HOST)
        assert not answered_by_the_switch(headers) and not is_slate(body) and b"not_live" not in body, (path, status)


def test_turning_the_switch_off_opens_the_pages_again_without_a_restart(switch):
    switch(True)
    assert is_slate(t.request("/board")[2])
    switch(False)
    assert not is_slate(t.request("/board")[2])
    assert not is_slate(t.request("/guide")[2])
