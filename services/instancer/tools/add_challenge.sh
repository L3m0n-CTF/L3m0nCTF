#!/usr/bin/env bash
# On a challenge host: build ONE challenge from a folder, pin it by digest in the local registry, put its stanza into /etc/l3mon/challenges.toml (replacing
# an earlier stanza of the same slug) and restart the agent so it reads the manifest. The way to add a challenge on a TEST host; from your laptop use
#   bash tools/ops/demo-round.sh add-challenge <folder> <slug> <http|tcp> <port> [mem_mb]      (it ships the folder and runs this)
#
#   bash add_challenge.sh <slug> <http|tcp> <port> <mem_mb> <folder.tgz>
#
# TEST HOST ONLY: the build uses the machine's own network (the host's isolation firewall drops the cloud resolver, so a build on Docker's default network
# cannot resolve a name), which is acceptable for the owner's own Dockerfiles and NOT for anybody else's. In production images are built in CI and pulled by
# digest (docs/deploy/instancer.md, section 5). The new manifest is checked with the agent's own loader BEFORE the agent is restarted, so a mistake cannot
# take the host down; the old manifest is kept as /etc/l3mon/challenges.toml.before-add. Running instances are not touched by the restart.
set -euo pipefail
SLUG="${1:?usage: add_challenge.sh <slug> <http|tcp> <port> <mem_mb> <folder.tgz>}"
PROTO="${2:?protocol}"
PORT="${3:?port}"
MEM="${4:?mem_mb}"
TGZ="${5:?folder.tgz}"
D="sudo docker"
MANIFEST=/etc/l3mon/challenges.toml

[[ "$SLUG" =~ ^[a-z0-9][a-z0-9_-]{0,47}$ ]] || { echo "the slug must be lower-case letters, digits, - and _ (at most 48)"; exit 2; }
[[ "$PROTO" == "http" || "$PROTO" == "tcp" ]] || { echo "the protocol is http or tcp"; exit 2; }
[[ "$PORT" =~ ^[0-9]+$ && "$PORT" -ge 1 && "$PORT" -le 65535 ]] || { echo "the port is a number from 1 to 65535"; exit 2; }
[[ "$MEM" =~ ^[0-9]+$ && "$MEM" -ge 32 && "$MEM" -le 1024 ]] || { echo "mem_mb is a number from 32 to 1024"; exit 2; }
[[ -f "$TGZ" ]] || { echo "no such file: $TGZ"; exit 2; }
curl -sf http://127.0.0.1:5000/v2/ > /dev/null || { echo "the local registry does not answer on 127.0.0.1:5000"; exit 1; }

WORK="$(mktemp -d /tmp/addchal.XXXXXX)"
trap 'rm -rf "$WORK"' EXIT
tar -xzf "$TGZ" -C "$WORK"
[[ -f "$WORK/Dockerfile" ]] || { echo "there is no Dockerfile at the top of the folder"; ls "$WORK" | head; exit 1; }
# a folder from Windows may carry CRLF line ends; a shell script or Dockerfile with them breaks inside the image (CI checks out LF)
find "$WORK" -type f \( -name Dockerfile -o -name "*.sh" \) -exec sed -i 's/\r$//' {} +

REF="localhost:5000/owner/$SLUG:t1"
echo "== building $SLUG (the first build pulls base images)"
$D build --network=host -q -t "$REF" "$WORK" > /dev/null
$D push -q "$REF" > /dev/null
IMAGE="$($D inspect --format '{{index .RepoDigests 0}}' "$REF")"
echo "   pinned: $IMAGE"

echo "== manifest"
sudo cp -p "$MANIFEST" "$MANIFEST.before-add"
OLD="$(mktemp)"
NEW="$(mktemp)"
sudo cat "$MANIFEST" > "$OLD" # the folder /etc/l3mon is not readable by a normal user
python3 - "$OLD" "$SLUG" "$IMAGE" "$PROTO" "$PORT" "$MEM" > "$NEW" <<'PY'
import re
import sys

path, slug, image, proto, port, mem = sys.argv[1:7]
text = open(path, encoding="utf-8").read()
head, *blocks = re.split(r"(?m)^(?=\[\[challenge\]\])", text)
kept = [b for b in blocks if not re.search(rf'(?m)^slug\s*=\s*"{re.escape(slug)}"\s*$', b)]
stanza = f'''[[challenge]]
slug = "{slug}"
image = "{image}"
protocol = "{proto}"
port = {port}
cpus = 0.25
mem_mb = {mem}
pids = 64
ttl_minutes = 15
max_per_team = 1
'''
sys.stdout.write(head.rstrip("\n") + "\n\n" + "".join(b.rstrip("\n") + "\n\n" for b in kept) + stanza)
PY
# the agent's own loader must accept it (it needs Python 3.11 for tomllib; the host has it)
( cd "$HOME/L3m0nCTF" && python3 -c "
import sys
from services.instancer.manifest import load_manifest
m = load_manifest(sys.argv[1])
print('   the agent\'s loader accepts it:', len(m.challenges), 'challenges')" "$NEW" )
sudo cp "$NEW" "$MANIFEST"
sudo chmod 0644 "$MANIFEST"
rm -f "$OLD" "$NEW"

echo "== restart the agent"
$D restart l3mon-challenge-host-agent-1 > /dev/null
for _ in $(seq 1 30); do
  TOKEN="$(sudo cat /etc/l3mon/secrets/INSTANCER_TOKEN)"
  BIND="$(sudo grep ^HOST_PRIVATE_BIND_IP /etc/l3mon/challenge-host.env | cut -d= -f2 | tr -d '"')"
  if curl -sf -m 3 -H "Authorization: Bearer $TOKEN" "http://$BIND:8300/v1/capacity" > /dev/null; then
    echo "ADDED: $SLUG ($PROTO, container port $PORT, ${MEM} MB, 15 minutes); the agent is back and has read the manifest"
    exit 0
  fi
  sleep 1
done
echo "the agent did not come back; the old manifest is at $MANIFEST.before-add"
$D logs --tail 20 l3mon-challenge-host-agent-1
exit 1
