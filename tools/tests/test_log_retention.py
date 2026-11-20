"""How long the log copy of the activity ledger is kept (docs/runbooks/17-activity-log.md).

Every line of the activity ledger is also printed to the platform's output, which the log shipper takes to Loki: the second copy that the database cannot erase. The owner's
rule (19 Nov 2026) is 30 days online, so Loki keeps 30 days (720 hours), and the compactor that enforces the limit stays on; the database copy is in every backup."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LOKI = (ROOT / "deploy" / "ops" / "loki" / "local-config.yaml").read_text(encoding="utf-8")
RUNBOOK = (ROOT / "docs" / "runbooks" / "17-activity-log.md").read_text(encoding="utf-8")


def test_loki_keeps_the_log_copy_for_thirty_days():
    assert re.search(r"^\s*retention_period:\s*720h\s*$", LOKI, re.MULTILINE), "30 days = 720 hours"


def test_the_compactor_that_enforces_the_limit_is_on():
    assert re.search(r"^\s*retention_enabled:\s*true\s*$", LOKI, re.MULTILINE)


def test_the_runbook_says_the_same_number():
    assert "30 days" in RUNBOOK
