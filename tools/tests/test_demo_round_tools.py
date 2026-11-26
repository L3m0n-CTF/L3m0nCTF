"""The instancer demo round (docs/runbooks/19-instancer-demo-round.md): its script, its runner and the laptop wrapper tools/ops/demo-round.sh.

Nothing real is touched: a fake `gcloud` first on PATH logs what it is asked and answers nothing. What is pinned: every command the script offers is written in the
runbook and in the wrapper's help (so the instructions cannot drift from the tool); the shell files are valid; the wrapper refuses a command that is not a plain
word and refuses to start without the project id; `run` ships exactly the two files to the platform VM over the IAP tunnel and starts the command there; and
`vm-stop` stops the challenge VM and never the platform VM.
"""
from __future__ import annotations

import ast
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
WRAPPER = ROOT / "tools" / "ops" / "demo-round.sh"
RUNNER = ROOT / "services" / "instancer" / "tools" / "run_platform_demo.sh"
SCRIPT = ROOT / "services" / "instancer" / "tools" / "platform_demo_round.py"
ADDER = ROOT / "services" / "instancer" / "tools" / "add_challenge.sh"
RUNBOOK = ROOT / "docs" / "runbooks" / "19-instancer-demo-round.md"
GIT_BASH = "C:/Program Files/Git/bin/bash.exe"
BASH = os.environ.get("L3MON_BASH") or (GIT_BASH if sys.platform == "win32" and os.path.exists(GIT_BASH) else "bash")

FAKE_GCLOUD = """#!/usr/bin/env bash
echo "gcloud $*" >> "$FAKE_LOG"
exit 0
"""


def script_commands() -> list[str]:
    """The commands the script's dispatch table offers, read from its text."""
    tree = ast.parse(SCRIPT.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and getattr(node.func, "attr", "") == "exit" and node.args and isinstance(node.args[0], ast.Call):
            call = node.args[0]
            if isinstance(call.func, ast.Subscript) and isinstance(call.func.value, ast.Dict):
                return [k.value for k in call.func.value.keys if isinstance(k, ast.Constant)]
    raise AssertionError("the dispatch table of platform_demo_round.py was not found")


def test_the_script_parses_and_offers_the_commands_the_runbook_uses():
    commands = script_commands()
    assert {"setup_programme", "setup", "functional", "burst", "cleanup", "counts"} <= set(commands)


def test_every_command_of_the_script_is_in_the_runbook_and_in_the_wrapper_help():
    runbook = RUNBOOK.read_text(encoding="utf-8")
    wrapper = WRAPPER.read_text(encoding="utf-8")
    help_text = wrapper.split("set -euo pipefail", 1)[0]
    for command in script_commands():
        assert command in runbook, f"the runbook does not mention `{command}`"
        assert command in help_text, f"the wrapper's help does not mention `{command}`"


def test_the_script_does_not_print_a_flag_or_a_password():
    """Flags are compared inside the platform and only a hash prefix or the word 'matches' is printed; test passwords are random and never shown."""
    text = SCRIPT.read_text(encoding="utf-8")
    assert "password" not in "\n".join(line for line in text.splitlines() if "out(" in line or "print(" in line).lower()

    def is_flag(node) -> bool:
        return (isinstance(node, ast.Name) and node.id in ("served", "fa")) or (isinstance(node, ast.Call) and getattr(node.func, "id", "") == "expected_flag")

    for node in ast.walk(ast.parse(text)):
        if isinstance(node, ast.Call) and getattr(node.func, "id", "") == "out":
            for arg in node.args:
                shown = [v.value for v in arg.values if isinstance(v, ast.FormattedValue)] if isinstance(arg, ast.JoinedStr) else [arg]
                for value in shown:
                    assert not is_flag(value), f"line {node.lineno}: a flag is printed as it is (a comparison, a length or a hash prefix is fine)"


def test_the_shell_files_are_valid_and_executable_in_the_repository():
    for path in (WRAPPER, RUNNER, ADDER):
        done = subprocess.run([BASH, "-n", str(path)], capture_output=True, text=True)
        assert done.returncode == 0, f"{path.name}: {done.stderr}"


@pytest.fixture
def fake(tmp_path):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    (bindir / "gcloud").write_text(FAKE_GCLOUD, encoding="utf-8", newline="\n")
    os.chmod(bindir / "gcloud", 0o755)
    log = tmp_path / "calls.log"
    log.write_text("", encoding="utf-8")
    env = dict(os.environ)
    env["PATH"] = str(bindir) + os.pathsep + env["PATH"]
    env["FAKE_LOG"] = str(log)
    env["CLOUDSDK_CORE_PROJECT"] = "a-test-project"
    return env, log


def run_wrapper(env, *args):
    return subprocess.run([BASH, str(WRAPPER), *args], capture_output=True, text=True, env=env)


def test_the_wrapper_needs_the_project_id(fake):
    env, log = fake
    env.pop("CLOUDSDK_CORE_PROJECT")
    done = run_wrapper(env, "status")
    assert done.returncode != 0 and "CLOUDSDK_CORE_PROJECT" in done.stderr
    assert log.read_text(encoding="utf-8") == ""


@pytest.mark.parametrize("bad", ["x; rm -rf /", "$(id)", "Up", "a b", "../x", ""])
def test_the_wrapper_refuses_a_command_that_is_not_a_plain_word(fake, bad):
    env, log = fake
    done = run_wrapper(env, "run", bad)
    assert done.returncode == 2
    assert log.read_text(encoding="utf-8") == "", "nothing may be sent to a machine for a refused command"


def test_run_ships_the_two_files_to_the_platform_vm_and_starts_the_command_there(fake):
    env, log = fake
    done = run_wrapper(env, "run", "counts")
    assert done.returncode == 0, done.stderr
    calls = log.read_text(encoding="utf-8").splitlines()
    assert len(calls) == 2
    scp, ssh = calls
    assert "compute scp" in scp and "--tunnel-through-iap" in scp and "--configuration=l3mon-new" in scp
    assert "platform_demo_round.py" in scp and "run_platform_demo.sh" in scp and "l3mon-app:/tmp/" in scp
    assert "compute ssh l3mon-app" in ssh and "--tunnel-through-iap" in ssh
    assert "bash /tmp/run_platform_demo.sh counts /tmp/platform_demo_round.py" in ssh
    assert "l3mon-chal" not in scp + ssh, "the platform's demo commands must never address the challenge VM"


def test_vm_stop_stops_the_challenge_vm_and_never_the_platform_vm(fake):
    env, log = fake
    done = run_wrapper(env, "vm-stop")
    assert done.returncode == 0, done.stderr
    calls = log.read_text(encoding="utf-8")
    assert "compute instances stop l3mon-chal" in calls
    assert "instances stop l3mon-app" not in calls


def test_an_unknown_action_prints_the_help_and_exits_2(fake):
    env, log = fake
    done = run_wrapper(env, "frobnicate")
    assert done.returncode == 2 and "vm-start" in done.stderr
    assert log.read_text(encoding="utf-8") == ""


@pytest.mark.parametrize("bad", ["", "closed", "OPEN", "open; id", "crew crew"])
def test_instance_gate_takes_only_open_or_crew(fake, bad):
    env, log = fake
    done = run_wrapper(env, "instance-gate", bad)
    assert done.returncode == 2
    assert log.read_text(encoding="utf-8") == ""


@pytest.mark.parametrize("mode", ["open", "crew"])
def test_instance_gate_sets_the_mode_and_restarts_only_the_central_router(fake, mode):
    """The first manual round on 22 Nov 2026 failed because the router rested in `crew` and nobody switched it to `open` for the round: every instance
    address answered 'Forbidden: these addresses are open to the organising crew only for now'. The runbook now says when, and this is the way."""
    env, log = fake
    done = run_wrapper(env, "instance-gate", mode)
    assert done.returncode == 0, done.stderr
    calls = log.read_text(encoding="utf-8").splitlines()
    assert len(calls) == 1 and "compute ssh l3mon-app" in calls[0] and "--tunnel-through-iap" in calls[0]
    assert f"INSTANCE_ACCESS_MODE={mode}" in calls[0]
    assert "tools/compose.sh up -d router" in calls[0], "only the router may be recreated: players are on the platform"
    assert "up -d --wait" not in calls[0] and "l3mon-chal" not in calls[0]


def test_the_runbook_tells_when_to_open_and_close_the_instance_gate():
    runbook = RUNBOOK.read_text(encoding="utf-8")
    assert "instance-gate open" in runbook and "instance-gate crew" in runbook
    assert runbook.index("instance-gate open") < runbook.index("## B."), "the instance gate must be opened before the by-hand steps"
    assert runbook.index("instance-gate crew") > runbook.index("## E."), "and put back at the end"
    assert "open to the organising crew only for now" in runbook, "the message a person sees when it was forgotten must be in the table of problems"


# ---- your own challenge: add-challenge (the host) and own_setup (the platform) --------------------------------------------------------------------------------

@pytest.fixture
def folder(tmp_path):
    d = tmp_path / "my challenge"
    d.mkdir()
    (d / "Dockerfile").write_text("FROM scratch\n", encoding="utf-8")
    return d


@pytest.mark.parametrize("args", [
    ("{folder}", "Bad Slug", "http", "8000"),
    ("{folder}", "../x", "http", "8000"),
    ("{folder}", "x", "udp", "8000"),
    ("{folder}", "x", "http", "80;id"),
    ("{folder}", "x", "http", "8000", "9"),
    ("/no/such/folder", "x", "http", "8000"),
    ("{folder}", "", "http", "8000"),
])
def test_add_challenge_refuses_what_it_cannot_ship_and_sends_nothing(fake, folder, args):
    env, log = fake
    done = run_wrapper(env, "add-challenge", *[a.replace("{folder}", str(folder)) for a in args])
    assert done.returncode == 2, done.stderr
    assert log.read_text(encoding="utf-8") == ""


def test_a_folder_without_a_dockerfile_is_refused(fake, tmp_path):
    env, log = fake
    (tmp_path / "empty").mkdir()
    done = run_wrapper(env, "add-challenge", str(tmp_path / "empty"), "x", "http", "8000")
    assert done.returncode == 2 and "Dockerfile" in done.stderr
    assert log.read_text(encoding="utf-8") == ""


def test_add_challenge_ships_the_folder_and_the_script_to_the_challenge_vm_only(fake, folder):
    env, log = fake
    done = run_wrapper(env, "add-challenge", str(folder), "own-delos", "tcp", "9004", "64")
    assert done.returncode == 0, done.stderr
    scp, ssh = log.read_text(encoding="utf-8").splitlines()
    assert "compute scp" in scp and "--tunnel-through-iap" in scp and "addchal-own-delos.tgz" in scp and "add_challenge.sh" in scp and "l3mon-chal:/tmp/" in scp
    assert "compute ssh l3mon-chal" in ssh and "--tunnel-through-iap" in ssh
    assert "bash /tmp/add_challenge.sh own-delos tcp 9004 64 /tmp/addchal-own-delos.tgz" in ssh
    assert "l3mon-app" not in scp + ssh, "adding a challenge to the host never touches the platform VM"


def test_run_hands_slugs_to_the_command_and_refuses_any_other_word(fake):
    env, log = fake
    assert run_wrapper(env, "run", "own_setup", "own-delos", "second_one").returncode == 0
    ssh = log.read_text(encoding="utf-8").splitlines()[-1]
    assert "bash /tmp/run_platform_demo.sh own_setup /tmp/platform_demo_round.py own-delos second_one" in ssh
    log.write_text("", encoding="utf-8")
    for bad in ("a b", "x;id", "$(id)", "Up", "../x", ""):
        assert run_wrapper(env, "run", "own_setup", bad).returncode == 2, bad
    assert log.read_text(encoding="utf-8") == "", "nothing may be sent to a machine for a refused word"


def test_the_adder_checks_the_manifest_with_the_agents_own_loader_before_it_restarts_the_agent():
    text = ADDER.read_text(encoding="utf-8")
    assert "from services.instancer.manifest import load_manifest" in text
    assert text.index("load_manifest") < text.index("docker restart") if "docker restart" in text else text.index("load_manifest") < text.index("$D restart")
    assert "challenges.toml.before-add" in text or '"$MANIFEST.before-add"' in text, "the old manifest is kept"
    assert "ttl_minutes = 15" in text and "max_per_team = 1" in text


def test_own_setup_is_silent_never_matches_a_real_challenge_and_cleanup_takes_the_channel_bonus_away():
    """The platform side of your own challenge: it must not tell players 'New on air' (the phase is live during the rehearsal window), must not grab a real
    challenge that happens to have the same name, and `cleanup` must take away the bonus a studio was paid for finishing the crew-test channel."""
    text = SCRIPT.read_text(encoding="utf-8")
    tree = ast.parse(text)
    functions = {n.name: n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}
    assert "own_setup" in functions
    setup_src = ast.get_source_segment(text, functions["setup"])
    assert "rc.announce = lambda" in setup_src, "release without the 'New on air' notice"
    assert 'category="crew-test"' in setup_src, "a challenge is found by name only among the crew-test ones"
    own_src = ast.get_source_segment(text, functions["own_setup"])
    assert '"Crew test "' in own_src and "setup(False, items)" in own_src
    cleanup_src = ast.get_source_segment(text, functions["cleanup"])
    assert "ChannelBonus" in cleanup_src and cleanup_src.index("ChannelBonus") < cleanup_src.index("db.session.delete(ch)"), "the awards go before the channel does"


# ---- the public raw-TCP rule: tcp-open (one address, never everybody) and tcp-close -------------------------------------------------------------------------
# On 25 Nov 2026 the owner's first raw-TCP test (Zero Cool) failed: the Google rule `l3mon-chal-allow-tcp-relays` rests OFF and nothing had switched it on all
# day (the audit log shows no change), and the address the page gave, h1.inst.l3m0nctf.xyz, pointed at Cloudflare. These two actions make the rule's use safe.

def test_tcp_open_switches_the_rule_on_for_one_address_only(fake):
    env, log = fake
    done = run_wrapper(env, "tcp-open", "203.0.113.7")
    assert done.returncode == 0, done.stderr
    calls = log.read_text(encoding="utf-8").splitlines()
    update = [c for c in calls if "firewall-rules update" in c]
    assert len(update) == 1
    assert "l3mon-chal-allow-tcp-relays" in update[0] and "--source-ranges=203.0.113.7/32" in update[0] and "--no-disabled" in update[0]
    assert "0.0.0.0/0" not in update[0], "the rule is never opened to everybody by this tool"
    assert all("instances" not in c for c in calls), "no machine is started, stopped or changed"
    assert "PUBLIC TCP RULE IS ON" in done.stdout


@pytest.mark.parametrize("bad", ["", "0.0.0.0", "0.0.0.0/0", "203.0.113.7/24", "256.1.1.1", "1.2.3", "a.b.c.d", "10.0.0.5", "127.0.0.1", "192.168.1.20", "172.16.4.4",
                                 "172.31.255.1", "169.254.169.254", "100.64.0.1", "100.127.1.1", "224.0.0.1", "255.255.255.255", "203.0.113.7 -x", "$(id)", "203.0.113.7;id"])
def test_tcp_open_refuses_anything_that_is_not_one_public_address_and_sends_nothing(fake, bad):
    env, log = fake
    done = run_wrapper(env, "tcp-open", bad)
    assert done.returncode == 2, (bad, done.stdout, done.stderr)
    assert log.read_text(encoding="utf-8") == ""


@pytest.mark.parametrize("good", ["1.2.3.4", "8.8.8.8", "100.63.255.255", "100.128.0.1", "172.15.0.1", "172.32.0.1", "203.0.113.7", "223.255.255.254"])
def test_tcp_open_accepts_public_addresses_next_to_the_private_ranges(fake, good):
    env, log = fake
    assert run_wrapper(env, "tcp-open", good).returncode == 0
    assert f"--source-ranges={good}/32" in log.read_text(encoding="utf-8")


def test_tcp_close_puts_the_rule_back_to_its_resting_state(fake):
    env, log = fake
    done = run_wrapper(env, "tcp-close")
    assert done.returncode == 0, done.stderr
    update = [c for c in log.read_text(encoding="utf-8").splitlines() if "firewall-rules update" in c]
    assert len(update) == 1 and "l3mon-chal-allow-tcp-relays" in update[0] and "--source-ranges=0.0.0.0/0" in update[0]
    assert update[0].rstrip().endswith("--disabled") and "--no-disabled" not in update[0], "the resting state is OFF"


def test_the_runbook_tells_the_dns_record_and_the_open_and_close_of_the_tcp_rule():
    runbook = RUNBOOK.read_text(encoding="utf-8")
    assert "tcp-open" in runbook and "tcp-close" in runbook
    assert "DNS only" in runbook and "h1.inst" in runbook and "34.180.12.8" in runbook, "the DNS-only record is what makes the name reach the host"
    assert runbook.index("tcp-open") < runbook.index("tcp-close"), "open before close"


# ---- tcp-tunnel: reaching a raw-TCP instance from a network that blocks high ports ------------------------------------------------------------------------
# 26 Nov 2026: the owner's laptop on the Amrita Wi-Fi (only 443 and 8080 get out; a control server on port 30000 failed too) could not reach the instance, while the
# platform VM could. An SSH forward through the IAP tunnel (443) and the platform VM gets out, and only the relay range may be forwarded.

FAKE_GCLOUD_IPS = """#!/usr/bin/env bash
echo "gcloud $*" >> "$FAKE_LOG"
case "$*" in
  *"instances describe l3mon-app"*) echo "203.0.113.10" ;;
  *"instances describe l3mon-chal"*) echo "${FAKE_CHAL_IP-198.51.100.20}" ;;
esac
exit 0
"""


@pytest.fixture
def fake_ips(fake):
    env, log = fake
    gcloud = Path(env["PATH"].split(os.pathsep)[0]) / "gcloud"
    gcloud.write_text(FAKE_GCLOUD_IPS, encoding="utf-8", newline="\n")
    return env, log


def test_tcp_tunnel_opens_the_rule_for_the_platform_vm_only_and_forwards_the_one_port(fake_ips):
    env, log = fake_ips
    done = run_wrapper(env, "tcp-tunnel", "30007")
    assert done.returncode == 0, done.stderr
    calls = log.read_text(encoding="utf-8").splitlines()
    update = [c for c in calls if "firewall-rules update" in c]
    assert len(update) == 1 and "--source-ranges=203.0.113.10/32" in update[0] and "--no-disabled" in update[0] and "0.0.0.0/0" not in update[0]
    ssh = [c for c in calls if "compute ssh" in c]
    assert len(ssh) == 1 and "l3mon-app" in ssh[0] and "--tunnel-through-iap" in ssh[0]
    assert ssh[0].rstrip().endswith("-- -N -L 30007:198.51.100.20:30007"), ssh[0]
    assert "l3mon-chal" not in ssh[0], "the tunnel runs through the platform VM, never into the challenge VM"
    assert all("instances start" not in c and "instances stop" not in c for c in calls)
    assert calls.index(update[0]) < calls.index(ssh[0]), "the rule is opened before the tunnel is used"
    assert "Stop-Process" in done.stdout and "-L 30007:" in done.stdout, "the tool prints the line that closes the forward (Ctrl+C does not)"


@pytest.mark.parametrize("bad", ["", "29999", "30200", "22", "8300", "443", "030000", "3000a", "30000;id", "$(id)", "30000 30001", "99999", "-1"])
def test_tcp_tunnel_forwards_only_the_relay_range_and_sends_nothing_otherwise(fake_ips, bad):
    env, log = fake_ips
    done = run_wrapper(env, "tcp-tunnel", bad)
    assert done.returncode == 2, (bad, done.stderr)
    assert log.read_text(encoding="utf-8") == ""


def test_tcp_tunnel_refuses_when_the_challenge_vm_has_no_address_and_opens_nothing(fake_ips):
    env, log = fake_ips
    env["FAKE_CHAL_IP"] = ""
    done = run_wrapper(env, "tcp-tunnel", "30000")
    assert done.returncode == 1 and "vm-start" in done.stderr
    calls = log.read_text(encoding="utf-8")
    assert "firewall-rules update" not in calls and "compute ssh" not in calls


def test_the_runbook_explains_the_blocked_network_and_the_tunnel():
    runbook = RUNBOOK.read_text(encoding="utf-8")
    assert "tcp-tunnel" in runbook and "Amrita" in runbook and "portquiz" in runbook
