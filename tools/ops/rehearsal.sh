#!/usr/bin/env bash
# tools/ops/rehearsal.sh - turn the crew's rehearsal on and off (docs/runbooks/15-rehearsal.md)
#
#   tools/ops/rehearsal.sh status
#   tools/ops/rehearsal.sh on [HOURS]     open the broadcast window for HOURS hours (default 6) and shut players out of the programme pages
#   tools/ops/rehearsal.sh off            put the real window back and open the pages again
#
# `on` and `off` change two files (python -m l3mon rehearsal does that and refuses anything odd), then make the machine notice: they render the settings,
# reload nginx (no break for anybody) and restart the platform container (about ten seconds). Run it on the machine, as the person who deploys.
#
# The ORDER matters, so that for no moment a player can reach the programmes while the rehearsal's window is open:
#   on:   nginx closes the pages first, then the platform opens the window.
#   off:  the platform goes back to the real window first (the board is empty again), then nginx opens the pages.
# If a step fails the script stops with the pages still closed; running `off` again (or `on`) is safe.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT_DIR"
ACTION="${1:-status}"
HOURS="${2:-6}"

if [[ -f /etc/l3mon/production.env ]]; then
  set -a
  # shellcheck disable=SC1091
  . /etc/l3mon/production.env
  set +a
fi

run_tool() { (cd tools && python3 -m l3mon rehearsal "$@"); }
render() { (cd tools && python3 -m l3mon config render ../config/event.toml --out ../deploy/compose/generated >/dev/null); }
reload_nginx() { tools/compose.sh exec -T nginx nginx -s reload; }
restart_platform() { tools/compose.sh up -d --wait --force-recreate --no-deps ctfd; }

case "$ACTION" in
  status)
    run_tool status
    ;;
  on)
    run_tool on --hours "$HOURS"
    render
    reload_nginx
    restart_platform
    echo "Done. The pages are closed to players and the window is open."
    run_tool status
    ;;
  off)
    run_tool off --keep-gate
    render
    restart_platform
    run_tool open-gate
    reload_nginx
    echo "Done. The real window is back and the pages are open."
    run_tool status
    echo "Now clear what the rehearsal left behind (docs/runbooks/15-rehearsal.md, 'Afterwards')."
    ;;
  *)
    echo "usage: $0 status | on [HOURS] | off" >&2
    exit 2
    ;;
esac
