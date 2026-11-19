#!/usr/bin/env bash
# tools/ops/activity-triggers.sh - make the database refuse UPDATE and DELETE on the activity ledger (the table l3mon_activity)
#
#   tools/ops/activity-triggers.sh status   say whether both triggers are in place (exit 0 yes, 1 no, 3 the table does not exist yet)
#   tools/ops/activity-triggers.sh apply    create them, as the database's root user; safe to repeat; run it at every deploy (after the platform has started once)
#
# Why this is a script and not only part of the platform's migration: production's MariaDB has binary logging on, and then it answers ERROR 1419 to CREATE TRIGGER
# from a user without the SUPER privilege, which the platform's database user does not have. The migration tries and carries on; this script makes sure.
# It uses CREATE TRIGGER IF NOT EXISTS and never drops a trigger first, so the table is never unprotected, not even for a moment.
# A person with the database's root password (or a compromised platform, which has all rights on its own database) can still drop a trigger; what then still
# finds a changed or a missing row is the hash chain, and what still holds the lines is the copy in the log (docs/runbooks/17-activity-log.md).
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

# SQL on standard input, as the database's root user, inside the database container (the same way the backup and restore scripts reach it)
sql() { tools/compose.sh exec -T db sh -c 'MYSQL_PWD="$(cat /run/secrets/DATABASE_ROOT_PASSWORD)" exec mariadb -N -uroot ctfd'; }

STATUS_SQL="SELECT (SELECT COUNT(*) FROM information_schema.TABLES WHERE TABLE_SCHEMA = 'ctfd' AND TABLE_NAME = 'l3mon_activity'), (SELECT COUNT(*) FROM information_schema.TRIGGERS WHERE TRIGGER_SCHEMA = 'ctfd' AND TRIGGER_NAME IN ('l3mon_activity_no_update', 'l3mon_activity_no_delete'));"

report() {
  local answer table triggers
  answer="$(printf '%s\n' "$STATUS_SQL" | sql | tr -d '\r' | tail -n 1)"
  table="$(printf '%s' "$answer" | cut -f1)"
  triggers="$(printf '%s' "$answer" | cut -f2)"
  if [[ "$table" != "1" ]]; then
    echo "The ledger table l3mon_activity is not there yet: start the platform once (its migration creates the table), then run this again." >&2
    return 3
  fi
  if [[ "$triggers" == "2" ]]; then
    echo "IN PLACE: the database refuses UPDATE and DELETE on the activity ledger (both triggers are in place)."
    return 0
  fi
  echo "NOT IN PLACE: only ${triggers:-0} of the 2 triggers exist on l3mon_activity. Run: tools/ops/activity-triggers.sh apply" >&2
  return 1
}

case "$ACTION" in
  status)
    report
    ;;
  apply)
    sql << 'SQL'
CREATE TRIGGER IF NOT EXISTS l3mon_activity_no_update BEFORE UPDATE ON l3mon_activity FOR EACH ROW SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'l3mon_activity is append-only';
CREATE TRIGGER IF NOT EXISTS l3mon_activity_no_delete BEFORE DELETE ON l3mon_activity FOR EACH ROW SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'l3mon_activity is append-only';
SQL
    report
    ;;
  *)
    echo "usage: $0 status | apply" >&2
    exit 2
    ;;
esac
