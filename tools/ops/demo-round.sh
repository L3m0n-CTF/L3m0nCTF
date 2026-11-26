#!/usr/bin/env bash
# tools/ops/demo-round.sh - the instancer demo round, from YOUR LAPTOP (docs/runbooks/19-instancer-demo-round.md has the whole procedure and what to expect).
#
#   bash tools/ops/demo-round.sh status          the two machines, the public TCP rule, and what the player gate says
#   bash tools/ops/demo-round.sh vm-start        start the challenge VM and wait until its host stack answers
#   bash tools/ops/demo-round.sh vm-stop         stop the challenge VM (every instance on it ends)
#   bash tools/ops/demo-round.sh tcp-check      READ-ONLY preflight for raw-TCP instances: does the name the page shows (h1.inst.l3m0nctf.xyz) point at the challenge VM, DNS only?
#                                                what does the public TCP rule say? Prints OK / NOTE / PROBLEM lines; exits 1 on a PROBLEM. Run it before every TCP test and on the day.
#   bash tools/ops/demo-round.sh tcp-open <your-public-IPv4>   switch the public raw-TCP rule ON for YOUR address only (a /32; ports 30000-30199 of the challenge VM).
#                                                Needed to reach a raw-TCP instance (Zero Cool, Jarvis) from your own computer: the rule rests OFF.
#   bash tools/ops/demo-round.sh tcp-tunnel <port>   reach a raw-TCP instance from a network that blocks high ports (the Amrita Wi-Fi allows 443 and 8080 only): opens
#                                                the rule for the PLATFORM VM's address only, then holds an SSH forward open: localhost:<port> on your computer
#                                                reaches the instance's port through the platform VM (a port from 30000 to 30199 only). Leave it running, use
#                                                `ncat localhost <port>` in another window. The tool prints the one-line command that closes it (Ctrl+C does not).
#   bash tools/ops/demo-round.sh tcp-close       put the rule back to its resting state (OFF, source 0.0.0.0/0). Run it when the TCP test is over.
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
# the public address of one of our machines (both have a reserved one, so it is there even while the machine is stopped)
ip_of() { g compute instances describe "$1" --zone="$ZONE" --format="value(networkInterfaces[0].accessConfigs[0].natIP)"; }

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

  tcp-check)
    # Read-only. Says in plain words whether a raw-TCP instance can be reached by a player's computer: does the name the page shows point at the challenge VM, and what the
    # firewall rule says. (26 Nov 2026: the owner made the record `chall.instance` instead of `h1.inst`, and for two attempts nobody could tell what was wrong.)
    NAME="${L3MON_TCP_NAME:-h1.inst.l3m0nctf.xyz}" # the name in the platform's /etc/l3mon/hosts.json (public_tcp_host)
    CHAL_IP="$(ip_of "$CHAL" 2>/dev/null || true)"
    STATE="$(g compute instances describe "$CHAL" --zone="$ZONE" --format="value(status)" 2>/dev/null || true)"
    # Ask Cloudflare's public resolver over HTTPS (port 443, so it works on a network that blocks DNS to outside resolvers; Windows' own nslookup timed out from Git Bash
    # on 26 Nov). Only the public name is sent. Answers are the `data` fields that are IPv4 addresses.
    RESP=""
    for _try in 1 2 3; do # one request in a while is dropped on a bad network: ask up to three times before saying the service cannot be reached
      RESP="$("${L3MON_CURL:-curl}" -s -m 12 -H 'accept: application/dns-json' "https://cloudflare-dns.com/dns-query?name=$NAME&type=A" 2>/dev/null || true)"
      [[ -n "$RESP" ]] && break
      sleep "${L3MON_RETRY_PAUSE:-2}"
    done
    ANSWERS="$(grep -oE '"data":"([0-9]{1,3}\.){3}[0-9]{1,3}"' <<<"$RESP" | grep -oE '([0-9]{1,3}\.){3}[0-9]{1,3}' | sort -u | tr '\n' ' ' | sed 's/ $//' || true)"
    RULE="$(g compute firewall-rules describe l3mon-chal-allow-tcp-relays --format="value(disabled,sourceRanges.list())" 2>/dev/null || true)"
    BAD=0
    echo "challenge VM $CHAL: ${STATE:-unknown}  (public address ${CHAL_IP:-unknown})"
    if [[ -z "$RESP" ]]; then
      echo "PROBLEM  could not ask the DNS service (no internet from this computer?): the name $NAME was not checked"; BAD=1
    elif [[ -n "$CHAL_IP" && "$ANSWERS" == "$CHAL_IP" ]]; then
      echo "OK       the name the page shows, $NAME, points at the challenge VM ($ANSWERS), DNS only"
    elif [[ -z "$ANSWERS" ]]; then
      echo "PROBLEM  the name the page shows, $NAME, does not exist in DNS yet: add an A record named h1.inst with address ${CHAL_IP:-<the address of the challenge VM>}, proxy status DNS only (grey cloud)"; BAD=1
    else
      echo "PROBLEM  the name the page shows, $NAME, points at $ANSWERS, not at the challenge VM (${CHAL_IP:-unknown}). If these are 104.x or 172.67.x it is Cloudflare's orange cloud (the wildcard record): add or rename a record to h1.inst, address ${CHAL_IP:-<the address of the challenge VM>}, DNS only"; BAD=1
    fi
    case "$RULE" in
      "True"*) echo "OK       the public TCP rule is OFF (resting state)" ;;
      "False"*) echo "NOTE     the public TCP rule is ON for: ${RULE#False}   (put it back with tcp-close when you are done)" ;;
      *) echo "PROBLEM  could not read the public TCP rule"; BAD=1 ;;
    esac
    exit "$BAD"
    ;;

  tcp-open)
    # The rule `l3mon-chal-allow-tcp-relays` is what lets the internet reach a raw-TCP instance. It rests OFF. This opens it for ONE address, never for everybody:
    # a single public IPv4 address, not a private one, as a /32. (On 25 Nov 2026 the owner's first raw-TCP test failed because the rule was off all day.)
    IP="${2:-}"
    OCT='(25[0-5]|2[0-4][0-9]|1[0-9]{2}|[1-9]?[0-9])'
    if ! [[ "$IP" =~ ^$OCT\.$OCT\.$OCT\.$OCT$ ]]; then
      echo "give YOUR public IPv4 address, e.g.  tcp-open 203.0.113.7   (open https://1.1.1.1/cdn-cgi/trace in a browser: the line 'ip=' is it)" >&2
      exit 2
    fi
    if [[ "$IP" =~ ^(0\.|10\.|127\.|169\.254\.|192\.168\.|172\.(1[6-9]|2[0-9]|3[01])\.|100\.(6[4-9]|[7-9][0-9]|1[01][0-9]|12[0-7])\.|22[4-9]\.|2[3-5][0-9]\.) ]]; then
      echo "$IP is not a public address (private, loopback, link-local, carrier-grade NAT or reserved): the rule needs the address the internet sees you as" >&2
      exit 2
    fi
    g compute firewall-rules update l3mon-chal-allow-tcp-relays --source-ranges="$IP/32" --no-disabled
    g compute firewall-rules describe l3mon-chal-allow-tcp-relays --format="table(name,disabled,sourceRanges.list():label=FROM,allowed[].map().firewall_rule().list():label=PORTS)"
    echo "THE PUBLIC TCP RULE IS ON for $IP only. Put it back with:  bash tools/ops/demo-round.sh tcp-close"
    ;;

  tcp-tunnel)
    # Why: on 26 Nov 2026 the owner's laptop on the Amrita Wi-Fi could not reach ANY raw-TCP instance (the network lets only 443 and 8080 out; a control server on
    # port 30000 failed too), while the same instance answered from the platform VM. SSH through the IAP tunnel uses 443, so it gets out. Only the relay range
    # is allowed, so this can never forward to another service of the challenge VM (its agent listens on 8300).
    PORT="${2:-}"
    if ! [[ "$PORT" =~ ^3[0-9]{4}$ ]] || (( PORT < 30000 || PORT > 30199 )); then
      echo "give the PORT the page shows for your instance (30000 to 30199), e.g.  tcp-tunnel 30000" >&2
      exit 2
    fi
    APP_IP="$(ip_of "$APP")"
    CHAL_IP="$(ip_of "$CHAL")"
    OCT='(25[0-5]|2[0-4][0-9]|1[0-9]{2}|[1-9]?[0-9])'
    for addr in "$APP_IP" "$CHAL_IP"; do
      [[ "$addr" =~ ^$OCT\.$OCT\.$OCT\.$OCT$ ]] || { echo "could not read a public address of $APP / $CHAL (is the challenge VM running? try: vm-start): '$addr'" >&2; exit 1; }
    done
    g compute firewall-rules update l3mon-chal-allow-tcp-relays --source-ranges="$APP_IP/32" --no-disabled
    echo "THE PUBLIC TCP RULE IS ON for the platform VM ($APP_IP) only."
    echo "TUNNEL: localhost:$PORT -> $CHAL_IP:$PORT through $APP (it opens in about 20 seconds). Keep this window open; in ANOTHER window run:  ncat localhost $PORT"
    echo "TO CLOSE IT (Ctrl+C was tried on 26 Nov and did NOT stop it), run this in another PowerShell window; this window then ends by itself:"
    echo "  Get-CimInstance Win32_Process | Where-Object { \$_.Name -eq 'putty.exe' -and \$_.CommandLine -match '-L $PORT:' } | ForEach-Object { Stop-Process -Id \$_.ProcessId -Force }"
    g compute ssh "$APP" --zone="$ZONE" --tunnel-through-iap --quiet -- -N -L "$PORT:$CHAL_IP:$PORT"
    echo "tunnel closed. Put the rule back with:  bash tools/ops/demo-round.sh tcp-close"
    ;;

  tcp-close)
    g compute firewall-rules update l3mon-chal-allow-tcp-relays --source-ranges="0.0.0.0/0" --disabled
    g compute firewall-rules describe l3mon-chal-allow-tcp-relays --format="table(name,disabled,sourceRanges.list():label=FROM,allowed[].map().firewall_rule().list():label=PORTS)"
    echo "the public TCP rule is OFF again (resting state)."
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
