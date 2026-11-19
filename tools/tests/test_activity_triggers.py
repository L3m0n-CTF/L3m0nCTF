"""tools/ops/activity-triggers.sh: make the database refuse UPDATE and DELETE on the activity ledger, as the database's root user.

Why a script and not only the migration: production's MariaDB has binary logging on, and then CREATE TRIGGER is refused (ERROR 1419) to a user without the SUPER
privilege, which the platform's database user does not have (reproduced on MariaDB 10.11 on 19 Nov 2026). The migration therefore only TRIES; this script, run as
root at every deploy, makes sure. It must be safe to repeat and must never leave the table unprotected for a moment (no DROP before the CREATE).

A fake tools/compose.sh stands in for the database: it records the SQL it is given and answers the status query with what the test says.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "tools" / "ops" / "activity-triggers.sh"
MIGRATION = ROOT / "plugins" / "l3mon_core" / "migrations" / "e9a4b2c5f306_add_the_activity_ledger_and_removals.py"
GIT_BASH = "C:/Program Files/Git/bin/bash.exe"
BASH = os.environ.get("L3MON_BASH") or (GIT_BASH if sys.platform == "win32" and os.path.exists(GIT_BASH) else "bash")

FAKE_COMPOSE = """#!/usr/bin/env bash
# stand-in for tools/compose.sh: records the SQL it is given on stdin, and answers the status query
echo "compose $*" >> "$FAKE_LOG"
sql="$(cat)"
printf '%s\\n' "$sql" >> "$FAKE_LOG.sql"
case "$sql" in
  *"information_schema.TRIGGERS"*) printf '%s\\n' "${FAKE_ANSWER:-1	2}" ;;
esac
"""


@pytest.fixture
def repo(tmp_path):
    (tmp_path / "tools" / "ops").mkdir(parents=True)
    shutil.copy(SCRIPT, tmp_path / "tools" / "ops" / "activity-triggers.sh")
    (tmp_path / "tools" / "compose.sh").write_text(FAKE_COMPOSE, newline="\n")
    (tmp_path / "tools" / "compose.sh").chmod(0o755)
    return tmp_path


def run(repo, action, answer=None):
    env = {k: v for k, v in os.environ.items() if not k.startswith(("L3MON_", "FAKE_"))}
    env.update({"FAKE_LOG": str(repo / "log").replace("\\", "/")})
    if answer is not None:
        env["FAKE_ANSWER"] = answer
    (repo / "log").write_text("")
    (repo / "log.sql").write_text("")
    done = subprocess.run([BASH, str(repo / "tools" / "ops" / "activity-triggers.sh").replace("\\", "/"), action], cwd=repo, env=env, capture_output=True, text=True, timeout=60)
    return done, (repo / "log.sql").read_text()


def trigger_statements(text: str) -> list[str]:
    text = "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))  # the comments talk about the statement too
    return [re.sub(r"\s+", " ", s).strip().rstrip(";") for s in re.findall(r"CREATE TRIGGER IF NOT EXISTS .*?(?=;|\"|\n\n|$)", text, re.DOTALL)]


def test_apply_creates_both_triggers_as_root_and_never_drops_one_first(repo):
    done, sql = run(repo, "apply")
    assert done.returncode == 0, done.stdout + done.stderr
    assert "l3mon_activity_no_update BEFORE UPDATE ON l3mon_activity" in sql and "l3mon_activity_no_delete BEFORE DELETE ON l3mon_activity" in sql
    assert "SIGNAL SQLSTATE '45000'" in sql and "append-only" in sql
    assert "IF NOT EXISTS" in sql and "DROP TRIGGER" not in sql.upper(), "idempotent, and the table is never unprotected between a drop and a create"


def test_apply_says_when_it_is_done_and_what_the_check_found(repo):
    done, _ = run(repo, "apply", answer="1\t2")
    assert "both triggers are in place" in done.stdout.lower()


def test_apply_fails_loudly_when_the_triggers_are_still_not_there_afterwards(repo):
    done, _ = run(repo, "apply", answer="1\t0")
    assert done.returncode == 1 and "NOT IN PLACE" in done.stdout + done.stderr


def test_apply_waits_for_nothing_and_says_so_when_the_ledger_table_does_not_exist_yet(repo):
    done, _ = run(repo, "apply", answer="0\t0")
    assert done.returncode == 3 and "start the platform once" in (done.stdout + done.stderr).lower()


@pytest.mark.parametrize("answer,code,word", [("1\t2", 0, "IN PLACE"), ("1\t1", 1, "NOT IN PLACE"), ("1\t0", 1, "NOT IN PLACE"), ("0\t0", 3, "not there yet")])
def test_status_reports_without_changing_anything(repo, answer, code, word):
    done, sql = run(repo, "status", answer=answer)
    assert done.returncode == code, done.stdout + done.stderr
    assert word in done.stdout + done.stderr
    assert "CREATE TRIGGER" not in sql, "status changes nothing"


def test_anything_else_prints_the_usage_and_touches_nothing(repo):
    done, sql = run(repo, "frobnicate")
    assert done.returncode == 2 and "usage" in (done.stdout + done.stderr).lower()
    assert sql.strip() == ""


def test_the_script_and_the_migration_create_exactly_the_same_triggers():
    """One definition, two places that make it: they must not drift apart."""
    script = [s for s in trigger_statements(SCRIPT.read_text(encoding="utf-8"))]
    migration = re.findall(r"CREATE TRIGGER IF NOT EXISTS l3mon_activity_no_\{verb\.lower\(\)\} BEFORE \{verb\} ON l3mon_activity FOR EACH ROW SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = '[^']+'", MIGRATION.read_text(encoding="utf-8"))
    assert len(migration) == 1 and len(script) == 2
    for verb in ("UPDATE", "DELETE"):
        expected = migration[0].replace("{verb.lower()}", verb.lower()).replace("{verb}", verb)
        assert expected in script, f"the script's {verb} trigger differs from the migration's"
