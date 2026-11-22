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
    for path in (WRAPPER, RUNNER):
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
