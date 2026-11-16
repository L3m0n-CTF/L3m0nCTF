"""A player's whole turn, in a real browser against the running stack: sign in, set up a studio, see a programme the crew put on air, open it, send a wrong
flag and the right one, see the TRP. The crew's side (a challenge, a channel, a programme, the release) is made through the crew's own API first; the same
steps through the page's forms are in test_release_setup_browser.py.

Needs the stack up with the event on air (tools/local-test.sh start makes exactly that; the development stack's settings put the event in the future, and
then the board is empty for everyone), Node 22 or newer, and Chrome (or set CHROME). Skipped, not failed, when the broadcast is not on, or Node or Chrome is
missing. It removes everything it made.

    python -m pytest tests/browser/test_player_board_browser.py -q
"""
from __future__ import annotations

import os
import secrets as pysecrets
import shutil
import subprocess
import sys
import time
import urllib.parse

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "integration"))

import test_release as r  # noqa: E402
import test_stack as t  # noqa: E402
from test_release_page_browser import have_a_browser  # noqa: E402

pytestmark = pytest.mark.integration

PLAYER = "boardwalker"
CHALLENGE = "Rehearsal Wombat"
VALUE = 150


def clean_up():
    for path, field, name in (("/api/v1/teams", "name", f"{PLAYER} studio"), ("/api/v1/users", "name", PLAYER)):
        status, out = r.admin("GET", f"{path}?view=admin&field={field}&q={urllib.parse.quote(name)}")
        for row in (out["data"] if status == 200 else []):
            if row["name"] == name:
                r.admin("DELETE", f"{path}/{row['id']}")
    status, out = r.admin("GET", f"/api/v1/challenges?view=admin&field=name&q={CHALLENGE.split()[0]}")
    for row in (out["data"] if status == 200 else []):
        if row["name"] == CHALLENGE:
            r.admin("DELETE", f"/api/v1/challenges/{row['id']}")
    r.in_ctfd(r.CLEANUP)


def on_air_now() -> bool:
    status, view = r.admin("GET", r.RELEASE)
    return status == 200 and view["data"]["phase"]["state"] == "live"


@pytest.mark.skipif(shutil.which("node") is None or not have_a_browser(), reason="needs Node and Chrome")
def test_a_player_signs_in_sets_up_a_studio_sends_a_flag_and_scores_in_a_real_browser():
    if not on_air_now():
        pytest.skip("the broadcast is not on: start the stack with tools/local-test.sh start (or open a rehearsal window)")
    clean_up()
    try:
        flag = "L3m0n" + "{rehearsal_" + pysecrets.token_hex(4) + "}"  # built from pieces: the repository's own scanner reads text, and this is made at run time
        status, out = r.admin("POST", "/api/v1/challenges", {"name": CHALLENGE, "category": "Web", "description": "Find the wombat.", "value": VALUE, "state": "hidden", "type": "standard"})
        assert status == 200, out
        cid = out["data"]["id"]
        assert r.admin("POST", "/api/v1/flags", {"challenge_id": cid, "content": flag, "type": "static", "data": ""})[0] == 200
        status, out = r.admin("PUT", r.PROGRAMMES, {"channels": [{"slug": "rehearsal", "name": "Rehearsal Row", "position": 1}],
                                                    "programmes": [{"challenge_id": cid, "channel": "rehearsal", "cell": 0, "number": 101, "slug": "rehearsal_wombat"}]})
        assert status == 200, out
        status, view = r.admin("GET", r.RELEASE)
        channel = view["data"]["channels"][0]
        r.change({"kind": "channel", "id": channel["id"], "mode": "release"}, {"kind": "programme", "id": channel["programmes"][0]["id"], "mode": "release"})
        password = pysecrets.token_urlsafe(18)
        status, out = r.admin("POST", "/api/v1/users", {"name": PLAYER, "email": f"{PLAYER}@example.com", "password": password, "type": "user", "verified": True})
        assert status == 200, out
        env = {**os.environ, "L3MON_BASE": f"http://localhost:{t.PORT}", "L3MON_PLAYER": PLAYER, "L3MON_PASSWORD": password, "L3MON_PROGRAMME": CHALLENGE,
               "L3MON_FLAG": flag, "L3MON_VALUE": str(VALUE)}
        done = subprocess.run(["node", os.path.join(HERE, "player_board_check.mjs")], env=env, capture_output=True, text=True, timeout=240)
        print(done.stdout)
        assert done.returncode == 0, done.stdout[-2500:] + done.stderr[-800:]
    finally:
        clean_up()
        time.sleep(0.2)
