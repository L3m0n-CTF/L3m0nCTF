#!/usr/bin/env bash
# On a challenge host: build the repository's sample challenges, pin each by digest in a local registry, write the TEST manifest and restart the agent so
# it reads it. Used before services/instancer/tools/real_host_proof.py. Safe to run again.
#
#   bash real_host_prepare.sh [fixtures-folder]      (default: ~/L3m0nCTF/services/instancer/tests/fixtures)
#
# The registry listens on 127.0.0.1:5000 only. The manifest is written to /etc/l3mon/challenges.toml and REPLACES what is there: this is for a test host.
set -euo pipefail
FIX="${1:-$HOME/L3m0nCTF/services/instancer/tests/fixtures}"
D="sudo docker"

for i in 1 2 3 4 5 6 7 8 9 10; do curl -sf http://127.0.0.1:5000/v2/ > /dev/null && break; sleep 1; done
curl -sf http://127.0.0.1:5000/v2/ > /dev/null || { echo "the local registry does not answer on 127.0.0.1:5000"; exit 1; }

pin() { # <name> -> the digest reference the manifest must use
  local name="$1" ref="localhost:5000/l3mon-fixture/$1:t1"
  $D build -q -t "$ref" "$FIX/$name" > /dev/null
  $D push -q "$ref" > /dev/null
  $D inspect --format '{{index .RepoDigests 0}}' "$ref"
}
WEB="$(pin web)"
TCP="$(pin tcp)"
PROBER="$(pin prober)"
STRESS="$(pin stress)"

stanza() { # slug image protocol port cpus mem pids ttl
  cat <<EOF

[[challenge]]
slug = "$1"
image = "$2"
protocol = "$3"
port = $4
cpus = $5
mem_mb = $6
pids = $7
ttl_minutes = $8
max_per_team = 1
EOF
}
{
  echo "# TEST manifest written by real_host_prepare.sh on $(date -u +%FT%TZ): the repository's sample challenges, digest-pinned from the local registry."
  stanza demo-web "$WEB" http 8080 0.25 64 64 45
  stanza demo-web2 "$WEB" http 8080 0.25 64 64 45
  stanza demo-web3 "$WEB" http 8080 0.25 64 64 45
  stanza demo-web-short "$WEB" http 8080 0.25 64 64 1
  stanza demo-tcp "$TCP" tcp 8000 0.25 64 64 45
  stanza demo-prober "$PROBER" http 8080 0.25 64 64 45
  stanza demo-stress "$STRESS" http 8080 0.25 128 64 45
} | sudo tee /etc/l3mon/challenges.toml > /dev/null
sudo chmod 0644 /etc/l3mon/challenges.toml
echo "manifest: $(grep -c '^slug' /etc/l3mon/challenges.toml) challenges"

$D restart l3mon-challenge-host-agent-1 > /dev/null
for i in $(seq 1 30); do
  TOKEN="$(sudo cat /etc/l3mon/secrets/INSTANCER_TOKEN)"
  BIND="$(sudo grep ^HOST_PRIVATE_BIND_IP /etc/l3mon/challenge-host.env | cut -d= -f2 | tr -d '"')"
  if curl -sf -m 3 -H "Authorization: Bearer $TOKEN" "http://$BIND:8300/v1/capacity" > /dev/null; then echo "agent is back and has read the manifest"; exit 0; fi
  sleep 1
done
echo "the agent did not come back"; $D logs --tail 20 l3mon-challenge-host-agent-1; exit 1
