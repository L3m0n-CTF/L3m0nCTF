#!/usr/bin/env bash
# tools/local-test.sh - the platform on your own computer, with the event already on air, for trying challenges. Nothing online is touched.
#
#   tools/local-test.sh start     build and start it (the first time takes a few minutes), then say where to go
#   tools/local-test.sh status    is it running, and where
#   tools/local-test.sh login     where the crew password for the local copy is kept
#   tools/local-test.sh stop      stop it, keeping everything that was made
#   tools/local-test.sh reset     stop it and delete everything it holds (needs --yes)
#
# It is reachable only from this computer, at http://localhost:8080 (the port is published on the loopback address only). The broadcast window is "from an
# hour ago, for 72 hours", registration is open and nobody has to confirm an e-mail address, so a person can register, set up a studio and play at once.
# On Windows run it in Git Bash. It needs Docker (with Compose v2) and Python 3.11 or newer. The guide is docs/runbooks/15-rehearsal.md.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"
ACTION="${1:-status}"
SETTINGS="config/local-test.toml"
PORT="${DEV_PORT:-8080}"

PY="${PYTHON:-}"
if [[ -z "$PY" ]]; then
  for candidate in python3 python; do
    if command -v "$candidate" >/dev/null 2>&1 && "$candidate" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' 2>/dev/null; then PY="$candidate"; break; fi
  done
fi

need() {
  command -v docker >/dev/null 2>&1 || { echo "Docker is not installed or not on the path." >&2; exit 1; }
  docker info >/dev/null 2>&1 || { echo "Docker is installed but not running: start Docker Desktop and try again." >&2; exit 1; }
  [[ -n "$PY" ]] || { echo "Python 3.11 or newer is needed (it only writes the settings file)." >&2; exit 1; }
}

case "$ACTION" in
  start)
    need
    (cd tools && "$PY" -m l3mon secrets generate --dir ../.secrets --only-missing >/dev/null)
    # the settings of this copy: the example file with the window moved to "now" and no e-mail check
    "$PY" - "$SETTINGS" <<'PYEOF'
import re, sys
from datetime import datetime, timedelta, timezone
now = datetime.now(timezone.utc).replace(second=0, microsecond=0)
fmt = "%Y-%m-%dT%H:%M:%SZ"
text = open("config/event.example.toml", encoding="utf-8").read()
text = re.sub(r'^(start\s*=\s*)"[^"]*"', lambda m: m.group(1) + '"' + (now - timedelta(hours=1)).strftime(fmt) + '"', text, count=1, flags=re.M)
text = re.sub(r'^(end\s*=\s*)"[^"]*"', lambda m: m.group(1) + '"' + (now + timedelta(hours=72)).strftime(fmt) + '"', text, count=1, flags=re.M)
text = re.sub(r'^(verify_emails\s*=\s*)\w+', r'\1false', text, count=1, flags=re.M)
open(sys.argv[1], "w", encoding="utf-8", newline="\n").write(text)
PYEOF
    (cd tools && "$PY" -m l3mon config render "../$SETTINGS" --out ../deploy/compose/generated >/dev/null)
    unset L3MON_ENV EXTRA_HOST ADMIN_NETS_FILE CREW_ONLY_FILE COMPOSE_PROFILES
    tools/compose.sh up -d --build --wait
    echo
    echo "The platform is running on this computer: http://localhost:${PORT}/"
    echo "  Crew pages (user 'organiser'):  http://localhost:${PORT}/admin   Release control: http://localhost:${PORT}/admin/l3mon/release"
    echo "  Players: register at http://localhost:${PORT}/register (no e-mail check here), set up a studio, play."
    echo "  Where is the crew password?  tools/local-test.sh login"
    ;;
  status)
    need
    tools/compose.sh ps --format "table {{.Service}}\t{{.Status}}" || true
    echo "Address: http://localhost:${PORT}/"
    ;;
  login)
    echo "Crew sign-in on this computer: user 'organiser', password in the file:"
    echo "  $ROOT_DIR/.secrets/PRESET_ADMIN_PASSWORD"
    echo "Open that file and copy the password from it (it is not printed here, and it only opens this local copy)."
    ;;
  stop)
    need
    tools/compose.sh stop
    ;;
  reset)
    need
    if [[ "${2:-}" != "--yes" ]]; then
      echo "This deletes everything the local copy holds (accounts, challenges, solves). Run: tools/local-test.sh reset --yes" >&2
      exit 1
    fi
    tools/compose.sh down -v
    echo "Everything the local copy held is gone. 'start' makes a fresh one."
    ;;
  *)
    echo "usage: $0 start | status | login | stop | reset --yes" >&2
    exit 2
    ;;
esac
