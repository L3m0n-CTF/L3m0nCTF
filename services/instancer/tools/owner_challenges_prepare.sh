#!/usr/bin/env bash
# On a challenge host: build the owner's live challenges, as delivered ("asis") and adapted for the instancer ("adapted"), pin each by digest in the local
# registry, and ADD their stanzas to /etc/l3mon/challenges.toml (after real_host_prepare.sh has written the sample ones). Test host only.
#
#   bash owner_challenges_prepare.sh /tmp/owner-chal        (a folder with asis/<name>/, adapted/<name>/ and solve/)
set -euo pipefail
ROOT="${1:?usage: owner_challenges_prepare.sh <folder with asis/ and adapted/>}"
D="sudo docker"
MANIFEST=/etc/l3mon/challenges.toml

# TEST HOST ONLY. The host's isolation firewall drops all traffic to 169.254.169.254, which is also Google's DNS resolver, so a build on Docker's default
# network cannot resolve a name (pip and apt fail). --network=host gives the build the machine's own network, which is acceptable for the owner's own
# Dockerfiles and NOT for anybody else's: a RUN step on the host network could reach the metadata address. In production images are built in CI and
# pulled by digest; nothing is built on a challenge host.
pin() { # <folder> <name> -> the digest reference
  local ref="localhost:5000/owner/$2:t1"
  $D build --network=host -q -t "$ref" "$1" > /dev/null
  $D push -q "$ref" > /dev/null
  $D inspect --format '{{index .RepoDigests 0}}' "$ref"
}
stanza() { # slug image protocol port mem
  cat <<EOF

[[challenge]]
slug = "$1"
image = "$2"
protocol = "$3"
port = $4
cpus = 0.25
mem_mb = $5
pids = 64
ttl_minutes = 15
max_per_team = 1
EOF
}

echo "== building (this pulls base images the first time)"
TURING="$(pin "$ROOT/asis/turing" turing)"
DEEP="$(pin "$ROOT/asis/deep" deep)"
DELOS_ASIS="$(pin "$ROOT/asis/delos" delos-asis)"
DELOS="$(pin "$ROOT/adapted/delos" delos)"
ZERO_ASIS="$(pin "$ROOT/asis/zero" zero-asis)"
ZERO="$(pin "$ROOT/adapted/zero" zero)"
JARVIS_ASIS="$(pin "$ROOT/asis/jarvis" jarvis-asis)"
JARVIS="$(pin "$ROOT/adapted/jarvis" jarvis)"
echo "   images built and pinned"

# drop an earlier owner block, then add the new one
sudo sed -i '/^# ---- owner challenges/,$d' "$MANIFEST"
{
  echo
  echo "# ---- owner challenges (written by owner_challenges_prepare.sh on $(date -u +%FT%TZ))"
  stanza owner-turing "$TURING" http 8000 128
  stanza owner-deep "$DEEP" http 8000 128
  stanza owner-delos-asis "$DELOS_ASIS" http 8000 128
  stanza owner-delos "$DELOS" http 8000 128
  stanza owner-zero-asis "$ZERO_ASIS" tcp 9004 64
  stanza owner-zero "$ZERO" tcp 9004 64
  stanza owner-jarvis-asis "$JARVIS_ASIS" tcp 9003 64
  stanza owner-jarvis "$JARVIS" tcp 9003 64
} | sudo tee -a "$MANIFEST" > /dev/null
sudo chmod 0644 "$MANIFEST"

$D restart l3mon-challenge-host-agent-1 > /dev/null
for i in $(seq 1 30); do
  TOKEN="$(sudo cat /etc/l3mon/secrets/INSTANCER_TOKEN)"
  BIND="$(sudo grep ^HOST_PRIVATE_BIND_IP /etc/l3mon/challenge-host.env | cut -d= -f2 | tr -d '"')"
  if curl -sf -m 3 -H "Authorization: Bearer $TOKEN" "http://$BIND:8300/v1/capacity" > /dev/null; then echo "agent is back and has read the manifest"; exit 0; fi
  sleep 1
done
echo "the agent did not come back"; $D logs --tail 20 l3mon-challenge-host-agent-1; exit 1
