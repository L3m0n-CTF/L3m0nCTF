"""The rehearsal switch (docs/runbooks/15-rehearsal.md): the crew tries the whole event on the real platform before the real event.

`on` does two things to two files and nothing else:
  - it moves the broadcast window of config/event.toml to "from ten minutes ago, for N hours", keeping a copy of the file as it was, so that the board,
    the flag form, hints and scoring work (before the real start the platform shows nobody anything);
  - it writes `default 1;` into nginx's crew-only file, so that the pages and routes that carry the programmes answer only on the crew host (and to the
    organisers' addresses), and every player who has registered sees a plain "not on air yet" page.
`off` puts the file back exactly as it was, writes `default 0;`, and deletes the copy.

Restarting what has to notice (render the settings, reload nginx, restart the platform) is tools/ops/rehearsal.sh. Standard library only.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

BACKUP_SUFFIX = ".before-rehearsal"
GATE_ON = "default 1;\n"
GATE_OFF = "default 0;\n"
FORMAT = "%Y-%m-%dT%H:%M:%SZ"
LEAD = timedelta(minutes=10)  # the window starts a little in the past, so that a clock that runs a minute behind does not matter
MIN_HOURS, MAX_HOURS = 1, 48


class RehearsalError(Exception):
    """A refusal, in plain words."""


def _pattern(key: str) -> re.Pattern:
    return re.compile(rf'^({key}\s*=\s*")([^"\n]*)(")', re.MULTILINE)


def _window(text: str) -> tuple[str, str]:
    found = []
    for key in ("start", "end"):
        match = _pattern(key).search(text)
        if match is None:
            raise RehearsalError(f'the settings file has no {key} = "..." line')
        found.append(match.group(2))
    return found[0], found[1]


def _with_window(text: str, start: str, end: str) -> str:
    for key, value in (("start", start), ("end", end)):
        text = _pattern(key).sub(lambda m, v=value: m.group(1) + v + m.group(3), text, count=1)
    return text


def _backup(event: Path) -> Path:
    return event.with_name(event.name + BACKUP_SUFFIX)


def _gate_word(gate: Path) -> str:
    try:
        for line in gate.read_text(encoding="utf-8").splitlines():
            words = line.split("#")[0].strip().rstrip(";").split()
            if words[:1] == ["default"] and words[-1:] == ["1"]:
                return "crew only"
    except OSError:
        pass
    return "open"


def status(event: Path, gate: Path) -> dict:
    start, end = _window(event.read_text(encoding="utf-8"))
    return {"start": start, "end": end, "rehearsing": _backup(event).exists(), "gate": _gate_word(gate)}


def turn_on(event: Path, gate: Path, hours, now: datetime | None = None) -> dict:
    if isinstance(hours, bool) or not isinstance(hours, int) or not MIN_HOURS <= hours <= MAX_HOURS:
        raise RehearsalError(f"hours must be a whole number from {MIN_HOURS} to {MAX_HOURS}")
    if _backup(event).exists():
        raise RehearsalError("a rehearsal is already on (the copy of the real settings is kept next to the settings file); turn it off first")
    original = event.read_text(encoding="utf-8")
    _window(original)  # refuses a file without the two times, before anything is written
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc).replace(second=0, microsecond=0)
    start, end = (now - LEAD).strftime(FORMAT), (now + timedelta(hours=hours)).strftime(FORMAT)
    _backup(event).write_bytes(event.read_bytes())
    event.write_text(_with_window(original, start, end), encoding="utf-8", newline="")
    gate.write_text(GATE_ON, encoding="utf-8")
    return {"start": start, "end": end, "hours": hours, "gate": "crew only"}


def turn_off(event: Path, gate: Path, keep_gate: bool = False) -> dict:
    """Put the real window back. With `keep_gate` the player pages stay closed: tools/ops/rehearsal.sh opens them (`open_gate`) only after the platform has been
    restarted with the real window, so that for no moment a player can reach the rehearsal's programmes while the rehearsal's window is still open."""
    copy = _backup(event)
    if not copy.exists():
        raise RehearsalError("no rehearsal is on (there is no copy of the real settings), so nothing was changed")
    event.write_bytes(copy.read_bytes())
    if not keep_gate:
        gate.write_text(GATE_OFF, encoding="utf-8")
    copy.unlink()
    start, end = _window(event.read_text(encoding="utf-8"))
    return {"start": start, "end": end, "gate": _gate_word(gate)}


def open_gate(gate: Path) -> dict:
    gate.write_text(GATE_OFF, encoding="utf-8")
    return {"gate": "open"}


def close_gate(gate: Path) -> dict:
    """Keep the player pages for the crew, with no change to the event window (before the event, while the board is being set up and looked at)."""
    gate.write_text(GATE_ON, encoding="utf-8")
    return {"gate": "crew only"}
