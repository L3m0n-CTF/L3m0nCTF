"""Integration tests for host preflight audit script tools/ops/preflight.sh."""
from __future__ import annotations

import json
import subprocess
import pytest
import test_stack as t

pytestmark = [pytest.mark.integration]

PREFLIGHT_SCRIPT = t.ROOT / "tools" / "ops" / "preflight.sh"


def test_preflight_script_exists_and_passes_on_running_stack():
    """Verify that tools/ops/preflight.sh executes cleanly on running stack with 0 FAIL."""
    res = subprocess.run(
        [t.BASH, str(PREFLIGHT_SCRIPT), "--env=dev"],
        capture_output=True,
        text=True,
        cwd=t.ROOT,
    )
    assert res.returncode == 0, f"Preflight failed: {res.stdout}\n{res.stderr}"
    assert "0 FAIL" in res.stdout
    assert "[PASS] secrets_privacy" in res.stdout
    assert "[PASS] flag_hmac_secret" in res.stdout
    assert "[PASS] metadata_block" in res.stdout
    assert "[PASS] compose_services" in res.stdout


def test_preflight_json_format_output():
    """Verify that --json produces valid parseable JSON with expected keys."""
    subprocess.run([t.BASH, str(t.ROOT / "tools" / "ops" / "maintenance.sh"), "off"], check=True)
    res = subprocess.run(
        [t.BASH, str(PREFLIGHT_SCRIPT), "--env=dev", "--json"],
        capture_output=True,
        text=True,
        cwd=t.ROOT,
    )
    assert res.returncode == 0
    data = json.loads(res.stdout)
    assert "pass" in data
    assert "fail" in data
    assert "results" in data
    assert data["fail"] == 0
    assert any(r["name"] == "metadata_block" and r["status"] == "PASS" for r in data["results"])


@pytest.mark.overlays
def test_preflight_catches_active_maintenance_mode():
    """Verify that preflight.sh reports FAIL when maintenance flag file is active."""
    # Turn on maintenance
    subprocess.run([t.BASH, str(t.ROOT / "tools" / "ops" / "maintenance.sh"), "on", "--message", "Preflight drill"], check=True)
    try:
        res = subprocess.run(
            [t.BASH, str(PREFLIGHT_SCRIPT), "--env=dev"],
            capture_output=True,
            text=True,
            cwd=t.ROOT,
        )
        assert res.returncode != 0
        assert "[FAIL] maintenance_mode" in res.stdout
        assert "ACTIVE" in res.stdout
    finally:
        subprocess.run([t.BASH, str(t.ROOT / "tools" / "ops" / "maintenance.sh"), "off"], check=True)


def test_preflight_production_guard_flags_preset_admin_token(tmp_path):
    """Verify that preflight checks fail in production mode if PRESET_ADMIN_TOKEN is present."""
    res = subprocess.run(
        [t.BASH, str(PREFLIGHT_SCRIPT), "--env=production"],
        capture_output=True,
        text=True,
        cwd=t.ROOT,
    )
    # In dev repo, PRESET_ADMIN_TOKEN exists, so in production mode it must FAIL
    assert res.returncode != 0
    assert "[FAIL] admin_token_guard" in res.stdout or "[FAIL] deploy_check" in res.stdout


def test_preflight_fails_while_the_channel_pages_are_closed_to_players_and_passes_when_they_are_open():
    """The crew-only switch must be open when the event starts; the pre-flight audit of the hour before is where it is caught."""
    switch = t.ROOT / "deploy" / "nginx" / "snippets" / "crew-only.conf"
    original = switch.read_bytes()
    try:
        switch.write_bytes(("default 1;" + chr(10)).encode())  # written in place: nginx's container sees the same file
        res = subprocess.run([t.BASH, str(PREFLIGHT_SCRIPT), "--env=dev"], capture_output=True, text=True, timeout=180, cwd=str(t.ROOT))
        assert "[FAIL] crew_only_switch" in res.stdout and "crew-gate.sh open" in res.stdout, res.stdout[-1500:]
        switch.write_bytes(original)
        res = subprocess.run([t.BASH, str(PREFLIGHT_SCRIPT), "--env=dev"], capture_output=True, text=True, timeout=180, cwd=str(t.ROOT))
        assert "[PASS] crew_only_switch" in res.stdout, res.stdout[-1500:]
    finally:
        switch.write_bytes(original)
