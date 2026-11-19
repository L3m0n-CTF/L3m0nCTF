"""tools/ops/gate.sh: open or close the crew-only gate from the owner's laptop with ONE command, and check from the outside that it really changed.

The event-day requirement (owner, 19 Nov 2026): the gate must open with a single command. The command logs in to the server through Google's IAP tunnel,
runs the server's own switch (tools/ops/crew-gate.sh), then asks the public site what a player would see, and says plainly whether the gate is open.

No real server is touched: a fake `gcloud` (which also plays the part of the switch on the server) and a fake `curl` (which answers like the site) sit first on PATH.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "tools" / "ops" / "gate.sh"
CREW_GATE = ROOT / "tools" / "ops" / "crew-gate.sh"
GIT_BASH = "C:/Program Files/Git/bin/bash.exe"
BASH = os.environ.get("L3MON_BASH") or (GIT_BASH if sys.platform == "win32" and os.path.exists(GIT_BASH) else "bash")

FAKE_GCLOUD = """#!/usr/bin/env bash
# stand-in for gcloud: logs the call; when the remote command is the switch, flips the fake site's state like the server would
echo "gcloud $*" >> "$FAKE_LOG"
cmd=""
while [ $# -gt 0 ]; do
  if [ "$1" = "--command" ]; then cmd="$2"; fi
  shift
done
case "$cmd" in
  *"crew-gate.sh open"*)   [ -n "${FAKE_NO_EFFECT:-}" ] || echo open > "$FAKE_STATE"; echo "Done. The channel pages answer everybody." ;;
  *"crew-gate.sh close"*)  [ -n "${FAKE_NO_EFFECT:-}" ] || echo closed > "$FAKE_STATE"; echo "Done. The channel pages answer only on the crew address." ;;
  *"crew-gate.sh status"*) echo "the switch file says: default $( [ "$(cat "$FAKE_STATE")" = closed ] && echo 1 || echo 0 );" ;;
esac
exit "${FAKE_GCLOUD_EXIT:-0}"
"""
FAKE_CURL = """#!/usr/bin/env bash
# stand-in for curl: answers like the site. Closed: 200 with the gate's header. Open: 302 to the sign-in page, without it.
echo "curl $*" >> "$FAKE_LOG"
[ -z "${FAKE_CURL_FAIL:-}" ] || exit 7
if [ "$(cat "$FAKE_STATE")" = closed ]; then
  printf 'HTTP/2 200\\r\\nx-l3mon-gate: crew-only\\r\\ncache-control: no-store\\r\\n\\r\\n'
else
  printf 'HTTP/2 302\\r\\nlocation: /login?next=%%2Fboard\\r\\n\\r\\n'
fi
"""


@pytest.fixture
def world(tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name, body in (("gcloud", FAKE_GCLOUD), ("curl", FAKE_CURL)):
        (bin_dir / name).write_text(body, newline="\n")
        (bin_dir / name).chmod(0o755)
    state, log = tmp_path / "state", tmp_path / "log"
    state.write_text("closed\n")
    log.write_text("")

    def run(*args: str, **env) -> subprocess.CompletedProcess:
        full = {k: v for k, v in os.environ.items() if not k.startswith(("L3MON_", "FAKE_"))}
        # Git Bash puts its own /mingw64/bin ahead of PATH, so the fake curl is named explicitly (the script takes L3MON_CURL, like L3MON_GCLOUD): without that
        # the tests would reach the real site
        full.update({"PATH": str(bin_dir) + os.pathsep + full["PATH"], "FAKE_STATE": str(state), "FAKE_LOG": str(log), "L3MON_GATE_SLEEP": "0",
                     "L3MON_CURL": str(bin_dir / "curl").replace("\\", "/")})
        full.update(env)
        return subprocess.run([BASH, str(SCRIPT).replace("\\", "/"), *args], env=full, capture_output=True, text=True, timeout=60)

    run.state = lambda: state.read_text().strip()
    run.log = lambda: log.read_text()
    run.set_state = lambda value: state.write_text(value + "\n")
    return run


def test_open_opens_the_gate_through_the_tunnel_and_confirms_from_the_outside(world):
    done = world("open")
    assert done.returncode == 0, done.stdout + done.stderr
    assert world.state() == "open"
    log = world.log()
    assert "compute ssh" in log and "--tunnel-through-iap" in log, "the server is reached through the IAP tunnel, not an open SSH port"
    assert "--configuration=l3mon-new" in log, "the owner's gcloud configuration, so the right project and account are used"
    assert "crew-gate.sh open" in log
    assert "GATE IS OPEN" in done.stdout


def test_open_says_so_loudly_when_the_server_obeyed_but_the_public_site_still_shuts_players_out(world):
    done = world("open", FAKE_NO_EFFECT="1")
    assert done.returncode == 1
    assert "STILL CLOSED" in done.stdout + done.stderr
    assert "crew-gate.sh open" in world.log()


def test_open_does_not_pretend_when_the_site_cannot_be_checked(world):
    done = world("open", FAKE_CURL_FAIL="1")
    assert done.returncode == 1
    assert "could not check" in (done.stdout + done.stderr).lower()


def test_open_fails_when_the_tunnel_fails_and_never_claims_success(world):
    done = world("open", FAKE_GCLOUD_EXIT="1")
    assert done.returncode != 0
    assert "GATE IS OPEN" not in done.stdout


def test_close_needs_an_explicit_yes_because_it_locks_the_players_out(world):
    world.set_state("open")
    refused = world("close")
    assert refused.returncode == 2 and world.state() == "open"
    assert "--yes" in refused.stdout + refused.stderr
    assert "gcloud" not in world.log(), "nothing was sent to the server without the yes"
    done = world("close", "--yes")
    assert done.returncode == 0, done.stdout + done.stderr
    assert world.state() == "closed" and "GATE IS CLOSED" in done.stdout


def test_status_shows_what_the_server_says_and_what_a_player_sees_and_changes_nothing(world):
    world.set_state("open")
    done = world("status")
    assert done.returncode == 0
    assert "default 0" in done.stdout and "GATE IS OPEN" in done.stdout
    assert world.state() == "open"
    assert "crew-gate.sh status" in world.log()


def test_a_closed_gate_is_reported_closed(world):
    done = world("status")
    assert "GATE IS CLOSED" in done.stdout


@pytest.mark.parametrize("args", [[], ["frobnicate"], ["open", "extra"]])
def test_anything_else_prints_the_usage_and_touches_nothing(world, args):
    done = world(*args)
    assert done.returncode == 2
    assert "usage" in (done.stdout + done.stderr).lower()
    assert "gcloud" not in world.log()


def test_a_laptop_without_gcloud_gets_a_plain_message(world):
    done = world("status", L3MON_GCLOUD="no-such-program-here")
    assert done.returncode == 3
    assert "gcloud" in (done.stdout + done.stderr).lower() and "install" in (done.stdout + done.stderr).lower()


def test_the_names_can_be_changed_with_environment_variables_without_editing_the_script(world):
    world("status", L3MON_VM="another-vm", L3MON_ZONE="europe-west1-b", L3MON_GCLOUD_CONFIG="other", L3MON_PLAY_URL="https://play.example.test")
    log = world.log()
    assert "another-vm" in log and "europe-west1-b" in log and "--configuration=other" in log and "https://play.example.test/board" in log


def test_the_script_holds_no_secret_and_no_project_id():
    text = SCRIPT.read_text(encoding="utf-8")
    assert "project-" not in text and "--project" not in text, "the project comes from the owner's gcloud configuration, not from a public repository"


# ------------------------------------------------------------------ the server's own script keeps a record of who used it

def test_the_server_switch_records_who_opened_or_closed_the_gate():
    text = CREW_GATE.read_text(encoding="utf-8")
    assert "logger -t l3mon-gate" in text, "the server's journal gets a line for every toggle"
    assert text.count("audit open") == 1 and text.count("audit close") == 1
