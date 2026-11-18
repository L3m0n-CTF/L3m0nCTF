#!/usr/bin/env bash
# tools/ops/mail-upstream.sh - inspect or selectively switch the mail relay upstream mode
#
#   tools/ops/mail-upstream.sh status    view current active mode, upstream configurations and delivery metrics
#   tools/ops/mail-upstream.sh auto      enable Brevo primary with automatic fallback to Gmail on quota/error (default)
#   tools/ops/mail-upstream.sh brevo     force Brevo only (Gmail fallback disabled)
#   tools/ops/mail-upstream.sh gmail     force Gmail only (Brevo disabled)
#   tools/ops/mail-upstream.sh hold      pause all outgoing mail (queues in spool)
#
# It updates the runtime mode file in the mail spool: no container restart required.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT_DIR"
ACTION="${1:-status}"

if [[ -f /etc/l3mon/production.env ]]; then
  set -a; . /etc/l3mon/production.env; set +a
fi

get_relay_cid() {
  local cid
  cid="$(docker ps -q --filter "name=mail-relay" 2>/dev/null | head -n 1 || true)"
  if [[ -n "$cid" ]]; then
    echo "$cid"
    return
  fi
  tools/compose.sh ps -q mail-relay 2>/dev/null || true
}

set_mode() {
  local target_mode="$1"
  local cid
  cid="$(get_relay_cid)"
  if [[ -z "$cid" ]]; then
    echo "Error: mail-relay service container is not running." >&2
    exit 1
  fi
  docker exec --user root "$cid" sh -c "echo '$target_mode' > /var/spool/l3mon-mail/mode"
}

case "$ACTION" in
  status)
    cid="$(get_relay_cid)"
    if [[ -z "$cid" ]]; then
      echo "mail-relay container is not running."
      exit 1
    fi
    docker exec "$cid" python -c "
import os, urllib.request
from pathlib import Path

mode_file = Path('/var/spool/l3mon-mail/mode')
current_mode = mode_file.read_text().strip() if mode_file.is_file() else os.environ.get('MAIL_UPSTREAM_MODE', 'auto')

print('=== Mail Relay Upstream Status ===')
print(f'Active Mode:        {current_mode.upper()}')
print(f'Primary Upstream:   {os.environ.get(\"UPSTREAM_SMTP_HOST\", \"(none)\")}:{os.environ.get(\"UPSTREAM_SMTP_PORT\", \"587\")} (User: {os.environ.get(\"UPSTREAM_SMTP_USER\", \"(none)\")})')
print(f'Fallback Upstream:  {os.environ.get(\"FALLBACK_SMTP_HOST\", \"(none)\")}:{os.environ.get(\"FALLBACK_SMTP_PORT\", \"587\")} (User: {os.environ.get(\"FALLBACK_SMTP_USER\", \"(none)\")})')

try:
    with urllib.request.urlopen('http://127.0.0.1:9025/metrics', timeout=2) as resp:
        print('--- Live Metrics ---')
        for line in resp.read().decode().splitlines():
            if line.startswith('l3mon_mail_'):
                print(f'  {line}')
except Exception as e:
    print(f'Metrics check: {e}')
"
    ;;
  auto|both)
    set_mode "auto"
    echo "Done. Upstream mode set to AUTO (Brevo primary with automatic fallback to Gmail SMTP)."
    ;;
  brevo|primary)
    set_mode "brevo"
    echo "Done. Upstream mode set to BREVO ONLY (Gmail fallback disabled)."
    ;;
  gmail|fallback)
    set_mode "gmail"
    echo "Done. Upstream mode set to GMAIL ONLY (Brevo disabled)."
    ;;
  hold|pause|off)
    set_mode "hold"
    echo "Done. Upstream mode set to HOLD (all outgoing deliveries paused; messages queue in spool)."
    ;;
  *)
    echo "usage: $0 status | auto | brevo | gmail | hold" >&2
    exit 2
    ;;
esac
