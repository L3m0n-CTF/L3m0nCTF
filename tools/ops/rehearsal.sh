#!/usr/bin/env bash
# tools/ops/rehearsal.sh - turn the crew's rehearsal on and off (docs/runbooks/15-rehearsal.md)
#
#   tools/ops/rehearsal.sh status
#   tools/ops/rehearsal.sh on [HOURS]     open the broadcast window for HOURS hours (default 6) and shut players out of the programme pages
#   tools/ops/rehearsal.sh off            put the real window back and open the pages again
#
# `on` and `off` change two files (python -m l3mon rehearsal does that and refuses anything odd), then make the machine notice: they render the settings,
# reload nginx (no break for anybody) and restart the platform container (about ten seconds). Run it on the machine, as the person who deploys.
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

case "$ACTION" in
  status)
    run_tool status
    ;;
  on | off)
    if [[ "$ACTION" == "on" ]]; then run_tool on --hours "$HOURS"; else run_tool off; fi
    (cd tools && python3 -m l3mon config render ../config/event.toml --out ../deploy/compose/generated >/dev/null)
    tools/compose.sh exec -T nginx nginx -s reload
    tools/compose.sh up -d --wait --force-recreate --no-deps ctfd
    echo "Done. The platform has the new window and nginx has the new switch."
    run_tool status
    if [[ "$ACTION" == "off" ]]; then
      echo "Now clear what the rehearsal left behind (docs/runbooks/15-rehearsal.md, 'Afterwards')."
    fi
    ;;
  *)
    echo "usage: $0 status | on [HOURS] | off" >&2
    exit 2
    ;;
esac
