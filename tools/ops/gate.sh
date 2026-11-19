#!/usr/bin/env bash
# tools/ops/gate.sh - open or close the crew-only gate from YOUR LAPTOP, with one command, and check from the outside that it really changed.
#
#   bash tools/ops/gate.sh open          THE COMMAND FOR THE MORNING OF THE EVENT: the channels answer everybody
#   bash tools/ops/gate.sh close --yes   the channels answer only the crew host; players are told "Not on air yet" (needs --yes: it locks players out)
#   bash tools/ops/gate.sh status        what the server's switch says, and what a player would see (changes nothing)
#
# What it does: logs in to the server through Google's IAP tunnel (no open SSH port), runs the server's own switch (tools/ops/crew-gate.sh: it changes one
# file and reloads nginx, nothing restarts), then asks the public site what a visitor gets for /board. A visitor who is shut out receives the gate's header
# `x-l3mon-gate: crew-only`; so "open" is only reported when that header is gone. If the check cannot be made it says so and exits with an error: it never
# guesses. The server writes who toggled the gate, and when, to its journal (`journalctl -t l3mon-gate`).
#
# It needs, on this laptop: bash (Git Bash on Windows), curl, and gcloud signed in with the configuration named below (the owner's `l3mon-new`), so the right
# project and account are used. No project id or secret is written here. Everything can be changed without editing the script:
#   L3MON_GCLOUD_CONFIG (l3mon-new)   L3MON_VM (l3mon-app)   L3MON_ZONE (asia-south1-a)   L3MON_PLAY_URL (https://play.l3m0nctf.xyz)
#   L3MON_REMOTE_DIR ($HOME/L3m0nCTF, the checkout on the server)   L3MON_GCLOUD (gcloud)   L3MON_CURL (curl)   L3MON_GATE_SLEEP (3, seconds between the outside checks)
set -euo pipefail

GCLOUD="${L3MON_GCLOUD:-gcloud}"
CURL="${L3MON_CURL:-curl}"
CONFIG="${L3MON_GCLOUD_CONFIG:-l3mon-new}"
VM="${L3MON_VM:-l3mon-app}"
ZONE="${L3MON_ZONE:-asia-south1-a}"
SITE="${L3MON_PLAY_URL:-https://play.l3m0nctf.xyz}"
REMOTE_DIR="${L3MON_REMOTE_DIR:-\$HOME/L3m0nCTF}" # sent as text: the server's own shell expands $HOME
PAUSE="${L3MON_GATE_SLEEP:-3}"

usage() {
  cat >&2 <<'EOF'
usage: bash tools/ops/gate.sh open
       bash tools/ops/gate.sh close --yes
       bash tools/ops/gate.sh status
EOF
  exit 2
}

ACTION="${1:-}"
case "$ACTION" in
  open | status) [[ $# -eq 1 ]] || usage ;;
  close)
    [[ $# -le 2 ]] || usage
    if [[ "${2:-}" != "--yes" ]]; then
      echo "Closing the gate shuts every player out of the channels (they are told \"Not on air yet\"). If you mean it, run:  bash tools/ops/gate.sh close --yes" >&2
      exit 2
    fi
    ;;
  *) usage ;;
esac

if ! command -v "$GCLOUD" > /dev/null 2>&1; then
  echo "gcloud was not found on this laptop. Install the Google Cloud CLI, sign in, and make sure the configuration \"$CONFIG\" exists (gcloud config configurations list)." >&2
  exit 3
fi

remote() {
  "$GCLOUD" --configuration="$CONFIG" compute ssh "$VM" --zone="$ZONE" --tunnel-through-iap --quiet --command "$1" \
    || { echo "The server could not be reached or the command failed (gcloud's message is above). Nothing is known to have changed." >&2; exit 1; }
}

# what a visitor to the site gets: closed (the gate's header is there), open (it is not), or unknown (the site did not answer properly)
outside() {
  local headers status
  if ! headers="$("$CURL" -s -o /dev/null -D - --max-time 20 "$SITE/board" 2> /dev/null)"; then
    echo unknown
    return
  fi
  status="$(sed -n '1s/^[^ ]* \([0-9]*\).*/\1/p' <<< "$headers")"
  if [[ ! "$status" =~ ^[0-9]+$ ]] || [[ "$status" -ge 500 ]]; then
    echo unknown
  elif grep -qi '^x-l3mon-gate: *crew-only' <<< "$headers"; then
    echo closed
  else
    echo open
  fi
}

# check from the outside until the site shows what we asked for (nginx applies the change within a second or two)
verify() {
  local want="$1" got="" try
  for try in 1 2 3 4 5; do
    got="$(outside)"
    [[ "$got" == "$want" ]] && return 0
    [[ "$try" -lt 5 ]] && sleep "$PAUSE"
  done
  case "$got" in
    unknown) echo "COULD NOT CHECK from the outside: $SITE did not answer properly. The server's own answer is above. Open $SITE/board in a private window to see what a player sees." >&2 ;;
    closed) echo "STILL CLOSED: the server ran the switch, but $SITE still answers \"Not on air yet\". Do not assume players can get in. Run: bash tools/ops/gate.sh status" >&2 ;;
    open) echo "STILL OPEN: the server ran the switch, but $SITE still shows the channels to a visitor. Run: bash tools/ops/gate.sh status" >&2 ;;
  esac
  return 1
}

stamp() { date -u +"%H:%M:%S UTC"; }

case "$ACTION" in
  status)
    remote "cd $REMOTE_DIR && bash tools/ops/crew-gate.sh status"
    case "$(outside)" in
      open) echo "GATE IS OPEN: a visitor reaches the channels. (checked from outside at $(stamp))" ;;
      closed) echo "GATE IS CLOSED: a visitor is told \"Not on air yet\"; the crew address sees everything. (checked from outside at $(stamp))" ;;
      *) echo "Could not check from the outside: $SITE did not answer properly." ;;
    esac
    ;;
  open)
    remote "cd $REMOTE_DIR && bash tools/ops/crew-gate.sh open"
    verify open
    echo "GATE IS OPEN: the channels answer players. (confirmed from outside at $(stamp))"
    ;;
  close)
    remote "cd $REMOTE_DIR && bash tools/ops/crew-gate.sh close"
    verify closed
    echo "GATE IS CLOSED: players are shut out of the channels; the crew address still sees everything. (confirmed from outside at $(stamp))"
    ;;
esac
