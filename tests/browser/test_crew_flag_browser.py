"""The main admin (a crew account with no studio) sends a flag from the board, in a real browser against the running stack: the page says why the flag was not
checked and what to do (it showed CTFd's bare "You don't have the permission to access the requested resource" until 16 November), the programme does not turn
solved, and nothing is recorded.

Needs the stack up with the event on air (tools/local-test.sh start makes exactly that), Node 22 or newer and Chrome. Skipped, not failed, when the broadcast is
not on. It removes everything it made.

    python -m pytest tests/browser/test_crew_flag_browser.py -q
"""
from __future__ import annotations

import os
import secrets as pysecrets
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

CHALLENGE = "Crew Wombat"


def clean_up():
    status, out = r.admin("GET", f"/api/v1/challenges?view=admin&field=name&q={CHALLENGE.split()[0]}")
    for row in (out["data"] if status == 200 else []):
        if row["name"] == CHALLENGE:
            r.admin("DELETE", f"/api/v1/challenges/{row['id']}")
    r.in_ctfd(r.CLEANUP)


@pytest.mark.skipif(shutil.which("node") is None or not have_a_browser(), reason="needs Node and Chrome")
def test_a_crew_account_with_no_studio_is_told_why_its_flag_was_not_checked_in_a_real_browser():
    status, view = r.admin("GET", r.RELEASE)
    if status != 200 or view["data"]["phase"]["state"] != "live":
        pytest.skip("the broadcast is not on: start the stack with tools/local-test.sh start")
    clean_up()
    try:
        flag = "L3m0n" + "{crew_" + pysecrets.token_hex(4) + "}"  # built from pieces: the repository's own scanner reads text, and this is made at run time
        status, out = r.admin("POST", "/api/v1/challenges", {"name": CHALLENGE, "category": "Web", "description": "Find the wombat.", "value": 120, "state": "hidden", "type": "standard"})
        assert status == 200, out
        cid = out["data"]["id"]
        assert r.admin("POST", "/api/v1/flags", {"challenge_id": cid, "content": flag, "type": "static", "data": ""})[0] == 200
        status, out = r.admin("PUT", r.PROGRAMMES, {"channels": [{"slug": "crewrow", "name": "Crew Row", "position": 1}],
                                                    "programmes": [{"challenge_id": cid, "channel": "crewrow", "cell": 0, "number": 101, "slug": "crew_wombat"}]})
        assert status == 200, out
        status, view = r.admin("GET", r.RELEASE)
        channel = view["data"]["channels"][0]
        r.change({"kind": "channel", "id": channel["id"], "mode": "release"}, {"kind": "programme", "id": channel["programmes"][0]["id"], "mode": "release"})
        session = t.Session()
        answer = session.open("/login", {"name": "organiser", "password": t.secret("PRESET_ADMIN_PASSWORD"), "_submit": "Submit", "nonce": session.nonce("/login")})
        assert answer.status == 302, f"sign-in answered {answer.status}"
        cookie = next(c.value for c in session.jar if c.name == "session")
        env = {**os.environ, "L3MON_BASE": f"http://localhost:{t.PORT}", "L3MON_COOKIE": cookie, "L3MON_PROGRAMME": CHALLENGE, "L3MON_FLAG": flag}
        done = subprocess.run(["node", os.path.join(HERE, "crew_flag_check.mjs")], env=env, capture_output=True, text=True, timeout=240)
        print(done.stdout)
        assert done.returncode == 0, done.stdout[-2500:] + done.stderr[-800:]
        status, out = r.admin("GET", f"/api/v1/challenges/{cid}/solves")
        assert status == 200 and out["data"] == [], f"nothing was recorded: {out}"
    finally:
        clean_up()
