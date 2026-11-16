"""The crew's view before the broadcast starts, in a real browser against the running stack: the Release control page says players see nothing yet, and the
board shows the crew the programme that is on air as a preview.

Needs the stack up with the broadcast NOT yet started (the production-like state: the development stack's example settings, or tools/local-test.sh with the
window moved to tomorrow), Node 22 or newer and Chrome. Skipped, not failed, when the broadcast is already on. It removes everything it made.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "integration"))

import test_release as r  # noqa: E402
import test_stack as t  # noqa: E402
from test_release_page_browser import have_a_browser  # noqa: E402

pytestmark = pytest.mark.integration

CHALLENGE = "Preview Wombat"


def clean_up():
    status, out = r.admin("GET", f"/api/v1/challenges?view=admin&field=name&q={CHALLENGE.split()[0]}")
    for row in (out["data"] if status == 200 else []):
        if row["name"] == CHALLENGE:
            r.admin("DELETE", f"/api/v1/challenges/{row['id']}")
    r.in_ctfd(r.CLEANUP)


@pytest.mark.skipif(shutil.which("node") is None or not have_a_browser(), reason="needs Node and Chrome")
def test_the_crew_sees_the_programme_on_the_board_before_the_start_and_the_page_says_players_do_not():
    status, view = r.admin("GET", r.RELEASE)
    if status != 200 or view["data"]["phase"]["state"] != "before":
        pytest.skip("the broadcast has already started: this checks the state before it")
    clean_up()
    try:
        status, out = r.admin("POST", "/api/v1/challenges", {"name": CHALLENGE, "category": "Web", "description": "Find the wombat.", "value": 120, "state": "hidden", "type": "standard"})
        assert status == 200, out
        cid = out["data"]["id"]
        status, out = r.admin("PUT", r.PROGRAMMES, {"channels": [{"slug": "preview", "name": "Preview Row", "position": 1}],
                                                    "programmes": [{"challenge_id": cid, "channel": "preview", "cell": 0, "number": 101, "slug": "preview_wombat"}]})
        assert status == 200, out
        status, view = r.admin("GET", r.RELEASE)
        channel = view["data"]["channels"][0]
        r.change({"kind": "channel", "id": channel["id"], "mode": "release"}, {"kind": "programme", "id": channel["programmes"][0]["id"], "mode": "release"})
        session = t.Session()
        answer = session.open("/login", {"name": "organiser", "password": t.secret("PRESET_ADMIN_PASSWORD"), "_submit": "Submit", "nonce": session.nonce("/login")})
        assert answer.status == 302, f"sign-in answered {answer.status}"
        cookie = next(c.value for c in session.jar if c.name == "session")
        env = {**os.environ, "L3MON_BASE": f"http://localhost:{t.PORT}", "L3MON_COOKIE": cookie, "L3MON_PROGRAMME": CHALLENGE}
        done = subprocess.run(["node", os.path.join(HERE, "crew_preview_check.mjs")], env=env, capture_output=True, text=True, timeout=240)
        print(done.stdout)
        assert done.returncode == 0, done.stdout[-2500:] + done.stderr[-800:]
    finally:
        clean_up()
