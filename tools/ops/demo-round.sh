#!/usr/bin/env bash
# tools/ops/demo-round.sh - the instancer demo round, from YOUR LAPTOP (docs/runbooks/19-instancer-demo-round.md has the whole procedure and what to expect).
#
#   bash tools/ops/demo-round.sh status          the two machines, the public TCP rule, and what the player gate says
#   bash tools/ops/demo-round.sh vm-start        start the challenge VM and wait until its host stack answers
#   bash tools/ops/demo-round.sh vm-stop         stop the challenge VM (every instance on it ends)
#   bash tools/ops/demo-round.sh run <command>   ship platform_demo_round.py to the platform VM and run one command of it there (see the list below)
#
# Commands of `run` (all go through the platform's own code and the real challenge host):
#   setup_programme   the channel "crew-test" with five sample programmes on air (no test studios: you sign in with your own test player)
#   setup             the same, plus two hidden test studios (test-inst-01, test-inst-02) for the automatic tests
#   counts            what exists now (users, challenges, programmes, solves, test users); changes nothing
#   probe             one signed-in call as test studio 1; changes nothing
#   functional        the 23 checks of one studio's whole life with an instance (needs `setup`)       about 2.5 minutes
#   burst             24 studios start at the same moment (creates 24 more hidden test studios)        about 1 minute
#   hostdown_during / hostdown_after / gone / after_gone / expected_tcp / reset    the parts of the host-down and recovery checks (the runbook says when)
#   cleanup           takes everything above away again (programmes, channel, sample challenges, test studios, their solves and instances)
#
# It needs, on this laptop: bash (Git Bash on Windows), gcloud signed in, and the Google Cloud project id in CLOUDSDK_CORE_PROJECT (no id is written here).
# The gate itself is opened and closed with tools/ops/gate.sh. Everything can be changed without editing: L3MON_GCLOUD (gcloud), L3MON_GCLOUD_CONFIG (l3mon-new),
# L3MON_ZONE (asia-south1-a), L3MON_APP_VM (l3mon-app), L3MON_CHAL_VM (l3mon-chal).
set -euo pipefail

GCLOUD="${L3MON_GCLOUD:-gcloud}"
CONFIG="${L3MON_GCLOUD_CONFIG:-l3mon-new}"
ZONE="${L3MON_ZONE:-asia-south1-a}"
APP="${L3MON_APP_VM:-l3mon-app}"
CHAL="${L3MON_CHAL_VM:-l3mon-chal}"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

usage() {
  sed -n '2,22p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//' >&2
  exit 2
}

ACTION="${1:-}"
[[ -n "$ACTION" ]] || usage
if [[ "$ACTION" != "help" ]]; then
  : "${CLOUDSDK_CORE_PROJECT:?set CLOUDSDK_CORE_PROJECT to the Google Cloud project id first (PowerShell: \$env:CLOUDSDK_CORE_PROJECT = \"<project id>\")}"
fi

g() { "$GCLOUD" --configuration="$CONFIG" "$@"; }
# a command given to a machine must NOT begin with a path (Git Bash would rewrite it); every one below begins with a word
on_vm() { g compute ssh "$1" --zone="$ZONE" --tunnel-through-iap --quiet --command "$2"; }

case "$ACTION" in
  help) usage ;;

  status)
    g compute instances list --format="table(name,machineType.basename(),status)"
    echo "public TCP rule (disabled must be True outside a test of raw-TCP challenges):"
    g compute firewall-rules list --filter="name~tcp-relays" --format="table(name,disabled,sourceRanges.list():label=FROM,allowed[].map().firewall_rule().list():label=PORTS)"
    bash "$HERE/tools/ops/gate.sh" status
    ;;

  vm-start)
    g compute instances start "$CHAL" --zone="$ZONE" --quiet
    echo "waiting for the challenge host's stack to answer ..."
    for _ in $(seq 1 30); do
      out="$(on_vm "$CHAL" 'sudo docker ps --format "{{.Names}} {{.Status}}" | tr "\n" ";"' 2>/dev/null | tail -1 || true)"
      if grep -q "l3mon-challenge-host-agent-1 Up" <<<"$out" && grep -q "l3mon-challenge-host-router-1 Up" <<<"$out"; then
        echo "up: $out"
        on_vm "$CHAL" 'sleep 4; sudo curl -s -H "Authorization: Bearer $(sudo cat /etc/l3mon/secrets/INSTANCER_TOKEN)" http://10.10.0.3:8300/v1/capacity; echo' | tail -1
        exit 0
      fi
      sleep 10
    done
    echo "the host's stack did not answer in 5 minutes: look at it with  gcloud compute ssh $CHAL --tunnel-through-iap" >&2
    exit 1
    ;;

  vm-stop)
    g compute instances stop "$CHAL" --zone="$ZONE" --quiet
    g compute instances list --format="table(name,machineType.basename(),status)"
    ;;

  run)
    CMD="${2:-}"
    [[ "$CMD" =~ ^[a-z_]+$ ]] || usage
    g compute scp --zone="$ZONE" --tunnel-through-iap --quiet \
      "$HERE/services/instancer/tools/platform_demo_round.py" "$HERE/services/instancer/tools/run_platform_demo.sh" "$APP":/tmp/
    on_vm "$APP" "sed -i 's/\\r\$//' /tmp/platform_demo_round.py /tmp/run_platform_demo.sh; bash /tmp/run_platform_demo.sh $CMD /tmp/platform_demo_round.py"
    ;;

  *) usage ;;
esac
