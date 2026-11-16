"""The rehearsal switch: the crew tries the whole event on the real platform before the real event (docs/runbooks/15-rehearsal.md).

`rehearsal on` opens the broadcast window around the present moment and closes the player pages to everybody but the crew (nginx's crew-only file);
`rehearsal off` puts the real window back, opens the pages again and says what is left to clean. Both only change two files; the machine's own
commands (render the settings, reload nginx, restart the platform) are in tools/ops/rehearsal.sh.
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))

from l3mon import config, rehearsal  # noqa: E402

NOW = datetime(2026, 11, 20, 9, 7, 41, tzinfo=timezone.utc)


@pytest.fixture()
def files(tmp_path):
    event = tmp_path / "event.toml"
    event.write_text((ROOT / "config" / "event.example.toml").read_text(encoding="utf-8"), encoding="utf-8")
    gate = tmp_path / "crew-only.conf"
    gate.write_text("default 0;\n", encoding="utf-8")
    return event, gate


def test_the_example_window_is_read_as_it_is_written(files):
    event, gate = files
    now = rehearsal.status(event, gate)
    assert (now["start"], now["end"]) == ("2026-11-28T04:30:00Z", "2026-11-28T16:30:00Z")
    assert now["rehearsing"] is False and now["gate"] == "open"


def test_on_opens_a_window_around_now_closes_the_player_pages_and_keeps_the_original(files):
    event, gate = files
    original = event.read_text(encoding="utf-8")
    summary = rehearsal.turn_on(event, gate, hours=6, now=NOW)
    assert (summary["start"], summary["end"]) == ("2026-11-20T08:57:00Z", "2026-11-20T15:07:00Z"), "from ten minutes ago, to the minute"
    text = event.read_text(encoding="utf-8")
    assert 'start = "2026-11-20T08:57:00Z"' in text and 'end = "2026-11-20T15:07:00Z"' in text
    assert "# 10:00 IST on 28 November 2026" in text, "the comments after the times stay"
    assert text.replace('2026-11-20T08:57:00Z', '2026-11-28T04:30:00Z').replace('2026-11-20T15:07:00Z', '2026-11-28T16:30:00Z') == original, "nothing else changed"
    assert gate.read_text(encoding="utf-8") == "default 1;\n"
    assert event.with_name(event.name + rehearsal.BACKUP_SUFFIX).read_text(encoding="utf-8") == original
    assert config.validate(config.load(event)) == [], "the file is still a valid settings file"
    assert rehearsal.status(event, gate)["rehearsing"] is True and rehearsal.status(event, gate)["gate"] == "crew only"


def test_on_twice_is_refused_so_the_real_window_can_never_be_lost(files):
    event, gate = files
    rehearsal.turn_on(event, gate, hours=6, now=NOW)
    during = event.read_text(encoding="utf-8")
    with pytest.raises(rehearsal.RehearsalError, match="already on"):
        rehearsal.turn_on(event, gate, hours=2, now=NOW)
    assert event.read_text(encoding="utf-8") == during


def test_off_puts_the_real_window_back_opens_the_pages_and_removes_the_copy(files):
    event, gate = files
    original = event.read_text(encoding="utf-8")
    rehearsal.turn_on(event, gate, hours=6, now=NOW)
    summary = rehearsal.turn_off(event, gate)
    assert event.read_text(encoding="utf-8") == original
    assert gate.read_text(encoding="utf-8") == "default 0;\n"
    assert not event.with_name(event.name + rehearsal.BACKUP_SUFFIX).exists()
    assert (summary["start"], summary["end"]) == ("2026-11-28T04:30:00Z", "2026-11-28T16:30:00Z")


def test_off_without_a_rehearsal_changes_nothing_and_still_opens_the_pages(files):
    event, gate = files
    gate.write_text("default 1;\n", encoding="utf-8")
    original = event.read_text(encoding="utf-8")
    with pytest.raises(rehearsal.RehearsalError, match="no rehearsal"):
        rehearsal.turn_off(event, gate)
    assert event.read_text(encoding="utf-8") == original and gate.read_text(encoding="utf-8") == "default 1;\n", "refused: nothing was touched"


@pytest.mark.parametrize("hours", [0, -3, 49, 1.5, "6", None, True])
def test_the_length_is_a_whole_number_of_hours_from_1_to_48(files, hours):
    event, gate = files
    original = event.read_text(encoding="utf-8")
    with pytest.raises(rehearsal.RehearsalError, match="hours"):
        rehearsal.turn_on(event, gate, hours=hours, now=NOW)
    assert event.read_text(encoding="utf-8") == original and gate.read_text(encoding="utf-8") == "default 0;\n"


def test_a_settings_file_without_the_two_times_is_refused_before_anything_is_written(files):
    event, gate = files
    event.write_text("[event]\nname = \"x\"\n", encoding="utf-8")
    with pytest.raises(rehearsal.RehearsalError, match="start"):
        rehearsal.turn_on(event, gate, hours=6, now=NOW)
    assert gate.read_text(encoding="utf-8") == "default 0;\n" and not event.with_name(event.name + rehearsal.BACKUP_SUFFIX).exists()


def test_the_command_line_runs_the_three_commands(files, capsys):
    from l3mon import cli

    event, gate = files
    assert cli.main(["rehearsal", "status", "--event", str(event), "--gate", str(gate)]) == 0
    assert "not on" in capsys.readouterr().out
    assert cli.main(["rehearsal", "on", "--hours", "3", "--event", str(event), "--gate", str(gate)]) == 0
    out = capsys.readouterr().out
    assert "crew only" in out and "3 hours" in out
    assert cli.main(["rehearsal", "on", "--event", str(event), "--gate", str(gate)]) == 1, "a second `on` is refused"
    assert cli.main(["rehearsal", "off", "--event", str(event), "--gate", str(gate)]) == 0
    assert "put back" in capsys.readouterr().out


def test_off_can_keep_the_pages_closed_until_the_platform_has_the_real_window_and_then_open_them(files):
    """The order that matters: the player pages must open only after the platform has been restarted with the real window, or for a few seconds a player
    could reach the rehearsal's programmes with the rehearsal's window still open (found by the security review of the first version)."""
    event, gate = files
    original = event.read_text(encoding="utf-8")
    rehearsal.turn_on(event, gate, hours=6, now=NOW)
    summary = rehearsal.turn_off(event, gate, keep_gate=True)
    assert event.read_text(encoding="utf-8") == original, "the real window is back in the file"
    assert gate.read_text(encoding="utf-8") == rehearsal.GATE_ON, "but the pages stay closed"
    assert summary["gate"] == "crew only"
    assert rehearsal.status(event, gate)["rehearsing"] is False and rehearsal.status(event, gate)["gate"] == "crew only"
    assert rehearsal.open_gate(gate)["gate"] == "open"
    assert gate.read_text(encoding="utf-8") == rehearsal.GATE_OFF


def test_the_command_line_has_the_two_steps_and_the_script_runs_them_in_the_safe_order(files, capsys):
    from l3mon import cli

    event, gate = files
    cli.main(["rehearsal", "on", "--hours", "2", "--event", str(event), "--gate", str(gate)])
    capsys.readouterr()
    assert cli.main(["rehearsal", "off", "--keep-gate", "--event", str(event), "--gate", str(gate)]) == 0
    assert gate.read_text(encoding="utf-8") == rehearsal.GATE_ON
    assert cli.main(["rehearsal", "open-gate", "--event", str(event), "--gate", str(gate)]) == 0
    assert gate.read_text(encoding="utf-8") == rehearsal.GATE_OFF
    # the shell script: for `off` the platform is restarted BEFORE nginx is told to open the pages; for `on` nginx closes them BEFORE the platform opens the window
    script = (ROOT / "tools" / "ops" / "rehearsal.sh").read_text(encoding="utf-8")
    off_part = script[script.index("\n  off)"):]
    assert off_part.index("restart_platform") < off_part.index("open-gate") < off_part.index("reload_nginx"), "off: platform first, then the pages"
    on_part = script[script.index("\n  on)"):script.index("\n  off)")]
    assert on_part.index("reload_nginx") < on_part.index("restart_platform"), "on: the pages close first, then the window opens"
    assert "up -d --wait --force-recreate --no-deps ctfd" in script and "nginx -s reload" in script


def test_the_player_pages_can_be_closed_and_opened_on_their_own_without_touching_the_window(files):
    """The channel pages can be kept for the crew (`close_gate`) before the event and opened for everybody (`open_gate`) when it starts, with no change to the
    event window and no restart of the platform: the switch is only nginx's file."""
    event, gate = files
    original = event.read_text(encoding="utf-8")
    assert rehearsal.close_gate(gate)["gate"] == "crew only" and gate.read_text(encoding="utf-8") == rehearsal.GATE_ON
    assert rehearsal.status(event, gate)["gate"] == "crew only" and rehearsal.status(event, gate)["rehearsing"] is False
    assert rehearsal.open_gate(gate)["gate"] == "open" and gate.read_text(encoding="utf-8") == rehearsal.GATE_OFF
    assert event.read_text(encoding="utf-8") == original, "the event window was never touched"


def test_the_command_line_closes_and_opens_the_pages_and_the_toggle_script_only_reloads_nginx(files, capsys):
    from l3mon import cli

    event, gate = files
    assert cli.main(["rehearsal", "close-gate", "--event", str(event), "--gate", str(gate)]) == 0
    assert "crew only" in capsys.readouterr().out and gate.read_text(encoding="utf-8") == rehearsal.GATE_ON
    assert cli.main(["rehearsal", "open-gate", "--event", str(event), "--gate", str(gate)]) == 0
    assert gate.read_text(encoding="utf-8") == rehearsal.GATE_OFF
    script = (ROOT / "tools" / "ops" / "crew-gate.sh").read_text(encoding="utf-8")
    assert "nginx -s reload" in script and "close-gate" in script and "open-gate" in script
    assert "force-recreate" not in script and "config render" not in script, "no restart of the platform and no new settings: only nginx's file and a reload"
    assert "set -euo pipefail" in script
