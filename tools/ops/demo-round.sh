#!/usr/bin/env bash
# tools/ops/demo-round.sh - the instancer demo round, from YOUR LAPTOP (docs/runbooks/19-instancer-demo-round.md has the whole procedure and what to expect).
#
#   bash tools/ops/demo-round.sh status          the two machines, the public TCP rule, and what the player gate says
#   bash tools/ops/demo-round.sh vm-start        start the challenge VM and wait until its host stack answers
#   bash tools/ops/demo-round.sh vm-stop         stop the challenge VM (every instance on it ends)
#   bash tools/ops/demo-round.sh run <command> [slug ...]   ship platform_demo_round.py to the platform VM and run one command of it there (see the list below)
#   bash tools/ops/demo-round.sh add-challenge <folder> <slug> <http|tcp> <port> [mem_mb]
#                                                put YOUR challenge on the challenge host: ships the folder (a Dockerfile at its top), builds and pins it there,
#                                                writes its stanza into the host's manifest and restarts the agent (1 to 3 minutes). Write the folder with
#                                                forward slashes (/d/work/x). Test hosts only (services/instancer/tools/add_challenge.sh says why).
#   bash tools/ops/demo-round.sh instance-gate open|crew   who may open an instance's address: open = everybody who has it (for a round with sample challenges
#                                                          and the player gate closed), crew = nobody until Cloudflare Access is set up (the resting state).
#                                                          Restarts only the central router; players are not affected. Without `open` every address answers
#                                                          "Forbidden: these addresses are open to the organising crew only for now".
#
# Commands of `run` (all go through the platform's own code and the real challenge host):
#   setup_programme   the channel "crew-test" with five sample programmes on air (no test studios: you sign in with your own test player)
#   setup             the same, plus two hidden test studios (test-inst-01, test-inst-02) for the automatic tests
#   counts            what exists now (users, challenges, programmes, solves, test users); changes nothing
#   probe             one signed-in call as test studio 1; changes nothing
#   functional        the 24 checks of one studio's whole life with an instance (needs `setup`)       about 2.5 minutes
#   burst             24 studios start at the same moment (creates 24 more hidden test studios)        about 1 minute
#   hostdown_during / hostdown_after / gone / after_gone / expected_tcp / reset    the parts of the host-down and recovery checks (the runbook says when)
#   own_setup <slug> [slug ...]   puts YOUR challenge (already on the host through add-challenge) on the channel "crew-test" and on air WITHOUT the "New on air"
#                     notice: a placeholder of 0 TRP named after the slug, so players see only that name. You test in your own studio, no test studio is made.
#   cleanup           takes everything above away again (programmes, channel, sample and own challenges, test studios, their solves, instances and any channel bonus)
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
  sed -n '2,/^set -euo pipefail/p' "${BASH_SOURCE[0]}" | sed '$d' | sed 's/^# \{0,1\}//' >&2
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
    echo "the instance gate on the platform's central router (open = everybody who has an address; crew = resting state):"
    on_vm "$APP" "docker inspect \"\$(docker ps -qf name=router-1 | head -1)\" --format '{{range .Config.Env}}{{println .}}{{end}}' | grep '^INSTANCE_ACCESS_MODE' || echo 'no central router is running'"
    ;;

  instance-gate)
    MODE="${2:-}"
    [[ "$MODE" == "open" || "$MODE" == "crew" ]] || usage
    on_vm "$APP" "sudo sed -i '/^INSTANCE_ACCESS_MODE=/d' /etc/l3mon/production.env && echo INSTANCE_ACCESS_MODE=$MODE | sudo tee -a /etc/l3mon/production.env > /dev/null && cd \"\$HOME/L3m0nCTF\" && set -a && . /etc/l3mon/production.env && set +a && tools/compose.sh up -d router 2>&1 | tail -3 && sleep 5 && docker inspect \"\$(docker ps -qf name=router-1 | head -1)\" --format '{{range .Config.Env}}{{println .}}{{end}}' | grep '^INSTANCE_ACCESS_MODE'"
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
    shift 2
    for word in "$@"; do [[ "$word" =~ ^[a-z0-9][a-z0-9_-]{0,47}$ ]] || usage; done # the only words a command takes are slugs
    g compute scp --zone="$ZONE" --tunnel-through-iap --quiet \
      "$HERE/services/instancer/tools/platform_demo_round.py" "$HERE/services/instancer/tools/run_platform_demo.sh" "$APP":/tmp/
    on_vm "$APP" "sed -i 's/\\r\$//' /tmp/platform_demo_round.py /tmp/run_platform_demo.sh; bash /tmp/run_platform_demo.sh $CMD /tmp/platform_demo_round.py $*"
    ;;

  add-challenge)
    FOLDER="${2:-}" SLUG="${3:-}" PROTO="${4:-}" PORT="${5:-}" MEM="${6:-128}"
    [[ -d "$FOLDER" && -f "$FOLDER/Dockerfile" ]] || { echo "the folder must exist and have a Dockerfile at its top (write it with forward slashes): $FOLDER" >&2; exit 2; }
    [[ "$SLUG" =~ ^[a-z0-9][a-z0-9_-]{0,47}$ && "$PROTO" =~ ^(http|tcp)$ && "$PORT" =~ ^[0-9]{1,5}$ && "$MEM" =~ ^[0-9]{2,4}$ ]] || usage
    PACK_DIR="$(mktemp -d)"
    trap 'rm -rf "$PACK_DIR"' EXIT
    tar -czf "$PACK_DIR/addchal-$SLUG.tgz" --exclude=.git --exclude=node_modules -C "$FOLDER" .
    echo "shipping the folder ($(du -h "$PACK_DIR/addchal-$SLUG.tgz" | cut -f1)) to the challenge VM ..."
    g compute scp --zone="$ZONE" --tunnel-through-iap --quiet "$PACK_DIR/addchal-$SLUG.tgz" "$HERE/services/instancer/tools/add_challenge.sh" "$CHAL":/tmp/
    on_vm "$CHAL" "sed -i 's/\\r\$//' /tmp/add_challenge.sh; bash /tmp/add_challenge.sh $SLUG $PROTO $PORT $MEM /tmp/addchal-$SLUG.tgz"
    ;;

  *) usage ;;
esac
