#!/usr/bin/env bash
# tools/ops/crew-gate.sh - keep the channel pages for the crew, or open them to everybody (docs/runbooks/15-rehearsal.md)
#
#   tools/ops/crew-gate.sh status
#   tools/ops/crew-gate.sh close    the board, the Guide, the scoreboard, the challenges, flags, hints and files answer only on the crew address
#                                   (crew.l3m0nctf.xyz); players on play.l3m0nctf.xyz are told "Not on air yet"
#   tools/ops/crew-gate.sh open     the same pages answer everybody (the normal state, and the state the event needs from its start)
#
# It changes one file and tells nginx to read it again: no restart, no break for anybody, and nothing about the event window changes (the rehearsal
# command, tools/ops/rehearsal.sh, is the one that moves the window). OPEN IT BEFORE THE EVENT STARTS: with the pages closed the real players cannot play.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT_DIR"
ACTION="${1:-status}"

if [[ -f /etc/l3mon/production.env ]]; then
  set -a
  # shellcheck disable=SC1091
  . /etc/l3mon/production.env
  set +a
fi

run_tool() { (cd tools && python3 -m l3mon rehearsal "$@"); }
reload_nginx() { tools/compose.sh exec -T nginx nginx -s reload; }

case "$ACTION" in
  status)
    run_tool status
    ;;
  close)
    run_tool close-gate
    reload_nginx
    echo "Done. The channel pages answer only on the crew address; players on the play address are told \"Not on air yet\"."
    ;;
  open)
    run_tool open-gate
    reload_nginx
    echo "Done. The channel pages answer everybody."
    ;;
  *)
    echo "usage: $0 status | close | open" >&2
    exit 2
    ;;
esac
