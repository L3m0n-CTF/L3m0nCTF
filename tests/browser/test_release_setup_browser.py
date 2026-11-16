"""The crew's "Set up the board" forms in a real browser, against the running development stack, starting from an EMPTY plan.

This is the first thing a crew member meets on a fresh platform: challenges made in CTFd that are on no channel, and no channel at all. The check makes a
channel, puts challenges on it, meets a refusal and a mistake in pasted text, and puts a channel and a programme on air, all with the page's own forms
and buttons. It needs the stack up (tools/compose.sh up -d --wait), Node 22 or newer and Chrome (or set CHROME); it signs in as the organiser from the
stack's own secrets file (nothing is printed) and removes everything it made. Skipped, not failed, when Node or Chrome is missing.

    python -m pytest tests/browser/test_release_setup_browser.py -q
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "integration"))

import test_release as r  # noqa: E402  (the helpers that talk to the stack)
import test_stack as t  # noqa: E402
from test_release_page_browser import have_a_browser  # noqa: E402

pytestmark = pytest.mark.integration

NAMES = ["Wombat Walk", "Numbat Night", "Quokka Quiz"]
CATEGORIES = ["Web", "Crypto", "Misc"]


def clean_up():
    for name in NAMES:
        status, out = r.admin("GET", f"/api/v1/challenges?view=admin&field=name&q={name.split()[0]}")
        for row in (out["data"] if status == 200 else []):
            if row["name"] == name:
                r.admin("DELETE", f"/api/v1/challenges/{row['id']}")
    r.in_ctfd(r.CLEANUP)  # no channels, no programmes, no audit lines: the plan is empty again


@pytest.mark.skipif(shutil.which("node") is None or not have_a_browser(), reason="needs Node and Chrome")
def test_the_setup_forms_make_a_channel_and_put_challenges_on_it_in_a_real_browser():
    clean_up()
    try:
        for n, name in enumerate(NAMES):
            status, out = r.admin("POST", "/api/v1/challenges", {"name": name, "category": CATEGORIES[n], "description": "plain words", "value": 100 * (n + 1), "state": "hidden", "type": "standard"})
            assert status == 200, out
        session = t.Session()
        answer = session.open("/login", {"name": "organiser", "password": t.secret("PRESET_ADMIN_PASSWORD"), "_submit": "Submit", "nonce": session.nonce("/login")})
        assert answer.status == 302, f"sign-in answered {answer.status}"
        cookie = next(c.value for c in session.jar if c.name == "session")
        env = {**os.environ, "L3MON_BASE": f"http://localhost:{t.PORT}", "L3MON_COOKIE": cookie}
        done = subprocess.run(["node", os.path.join(HERE, "release_setup_check.mjs")], env=env, capture_output=True, text=True, timeout=240)
        print(done.stdout)
        assert done.returncode == 0, done.stdout[-2500:] + done.stderr[-800:]

        # what the page did is what the platform holds: the plan has the channels and the programmes, and the two things put on air are on air
        status, view = r.admin("GET", r.RELEASE)
        by_slug = {c["slug"]: c for c in view["data"]["channels"]}
        assert set(by_slug) == {"test-street", "json-square"}, list(by_slug)
        street = by_slug["test-street"]
        assert street["state"] == "released" and [(p["slug"], p["number"], p["cell"], p["difficulty"], p["delivery"]) for p in street["programmes"]] == [
            ("wombat_walk", 101, 0, "medium", "static_shared"), ("numbat_night", 102, 1, "medium", "static_shared")]
        assert [p["state"] for p in street["programmes"]] == ["released", "withheld"], "only the programme that was released is on air"
        loose = [x["name"] for x in view["data"]["loose"]]
        assert "Quokka Quiz" in loose and "Wombat Walk" not in loose and "Numbat Night" not in loose, "the challenge nobody placed is still on no channel"
    finally:
        clean_up()
