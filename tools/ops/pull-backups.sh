#!/usr/bin/env bash
# tools/ops/pull-backups.sh - copy the platform's encrypted backups to THIS computer: the copy that survives the loss of the VM.
#
#   bash tools/ops/pull-backups.sh [--dest FOLDER] [--newest N] [--since STAMP] [--identity KEYFILE] [--keep N]
#
#   --dest FOLDER      where the copies go (default: $HOME/l3mon-backups, or L3MON_BACKUP_DEST). Outside every repository, please.
#   --newest N         at most the N newest backups on the VM (default 48: a day of half-hourly backups)
#   --since STAMP      only backups made at or after STAMP, written like 20261126T154000Z (use it to skip backups that were made before this computer's key was added)
#   --identity FILE    the age PRIVATE key of this computer: the newest copy is then opened and listed, which proves the copy can be restored
#   --keep N           keep the newest N copies here and delete older ones (default 200)
#
# What it does: asks the VM which backups it holds (`backup-<UTC time>.tar.age`, one every 30 minutes, kept 7 days, each with a `.sha256` file), copies the ones this
# computer does not have yet (through the IAP tunnel, no open port), checks every copy against its checksum and its encryption header, and says what it found.
# It only READS on the VM; it never changes anything there. It needs, here: bash (Git Bash on Windows), gcloud signed in with the configuration of the project, the
# project id in CLOUDSDK_CORE_PROJECT, and `age` and `tar` for --identity. Overrides: L3MON_GCLOUD, L3MON_GCLOUD_CONFIG (l3mon-new), L3MON_ZONE (asia-south1-a),
# L3MON_APP_VM (l3mon-app), L3MON_BACKUP_REMOTE_DIR (backups/sink/full, relative to the VM user's home).
#
# A backup holds the whole database (names, e-mail addresses, password hashes, the activity ledger): it is encrypted, and the key that opens it must be kept apart from
# the copies (a different place: a password manager, a USB stick). Without that key a copy is only noise.
set -euo pipefail

GCLOUD="${L3MON_GCLOUD:-gcloud}"
CONFIG="${L3MON_GCLOUD_CONFIG:-l3mon-new}"
ZONE="${L3MON_ZONE:-asia-south1-a}"
VM="${L3MON_APP_VM:-l3mon-app}"
REMOTE_DIR="${L3MON_BACKUP_REMOTE_DIR:-backups/sink/full}"
DEST="${L3MON_BACKUP_DEST:-$HOME/l3mon-backups}"
NEWEST=48
KEEP=200
SINCE=""
IDENTITY=""

usage() { sed -n '2,/^set -euo pipefail/p' "${BASH_SOURCE[0]}" | sed '$d' | sed 's/^# \{0,1\}//' >&2; exit 2; }

while [[ $# -gt 0 ]]; do
  case "$1" in
    --dest) DEST="${2:-}"; shift 2 ;;
    --newest) NEWEST="${2:-}"; shift 2 ;;
    --since) SINCE="${2:-}"; shift 2 ;;
    --identity) IDENTITY="${2:-}"; shift 2 ;;
    --keep) KEEP="${2:-}"; shift 2 ;;
    -h | --help) usage ;;
    *) echo "unknown option: $1" >&2; usage ;;
  esac
done
[[ "$NEWEST" =~ ^[0-9]{1,4}$ && "$NEWEST" -ge 1 ]] || { echo "--newest takes a number from 1 to 9999" >&2; exit 2; }
[[ "$KEEP" =~ ^[0-9]{1,4}$ && "$KEEP" -ge 1 ]] || { echo "--keep takes a number from 1 to 9999" >&2; exit 2; }
[[ -z "$SINCE" || "$SINCE" =~ ^[0-9]{8}T[0-9]{6}Z$ ]] || { echo "--since is written like 20261126T154000Z" >&2; exit 2; }
[[ -n "$DEST" ]] || { echo "--dest needs a folder" >&2; exit 2; }
[[ "$REMOTE_DIR" =~ ^[A-Za-z0-9._/-]+$ && "$REMOTE_DIR" != /* && "$REMOTE_DIR" != *..* ]] || { echo "the remote folder must be a plain relative path" >&2; exit 2; }
: "${CLOUDSDK_CORE_PROJECT:?set CLOUDSDK_CORE_PROJECT to the Google Cloud project id first (PowerShell: \$env:CLOUDSDK_CORE_PROJECT = \"<project id>\")}"
if [[ -n "$IDENTITY" ]]; then
  [[ -f "$IDENTITY" ]] || { echo "no such key file: $IDENTITY" >&2; exit 2; }
  command -v age > /dev/null || { echo "--identity needs the age tool (Scoop: scoop install age)" >&2; exit 2; }
fi

g() { "$GCLOUD" --configuration="$CONFIG" "$@"; }
mkdir -p "$DEST"

echo "== asking $VM which backups it holds"
# one call: the names AND every checksum (a line `<name>.sha256 <hash>  <name>` for each), so that no checksum file has to be copied one by one
listing="$(g compute ssh "$VM" --zone="$ZONE" --tunnel-through-iap --quiet --command "cd $REMOTE_DIR && ls -1 && for f in backup-*.tar.age.sha256; do printf 'SUM %s %s\\n' \"\$f\" \"\$(cat \"\$f\")\"; done" | tr -d '\r')"
sums="$(grep '^SUM ' <<<"$listing" || true)"
all="$(grep -E '^backup-[0-9]{8}T[0-9]{6}Z\.tar\.age$' <<<"$listing" | sort || true)"
[[ -n "$all" ]] || { echo "the VM holds no backup in $REMOTE_DIR" >&2; exit 1; }
echo "   the VM holds $(wc -l <<<"$all" | tr -d ' ') backups, newest: $(tail -1 <<<"$all")"

wanted="$all"
if [[ -n "$SINCE" ]]; then wanted="$(awk -v s="backup-$SINCE" '$0 >= s' <<<"$wanted")"; fi
wanted="$(tail -n "$NEWEST" <<<"$wanted")"
[[ -n "$wanted" ]] || { echo "no backup at or after $SINCE yet (wait for the next half hour)" >&2; exit 1; }

missing=()
while IFS= read -r name; do
  [[ -z "$name" ]] && continue
  if [[ ! -s "$DEST/$name" || ! -s "$DEST/$name.sha256" ]]; then missing+=("$name"); fi
done <<<"$wanted"
echo "== $(wc -l <<<"$wanted" | tr -d ' ') wanted, $((${#missing[@]})) not here yet"

# copy the missing ones, ONE file per call (gcloud copies through PuTTY on Windows, which takes a single remote source), and write each checksum file from the listing
for name in "${missing[@]+"${missing[@]}"}"; do
  g compute scp --zone="$ZONE" --tunnel-through-iap --quiet "$VM:$REMOTE_DIR/$name" "$DEST/"
  line="$(grep -F "SUM $name.sha256 " <<<"$sums" | head -1 || true)"
  [[ -n "$line" ]] && printf '%s\n' "${line#SUM $name.sha256 }" > "$DEST/$name.sha256"
done

# check every wanted copy: its checksum and the encryption header
sha() { if command -v sha256sum > /dev/null; then sha256sum "$1" | cut -d' ' -f1; else shasum -a 256 "$1" | cut -d' ' -f1; fi; }
good=0
bad=0
while IFS= read -r name; do
  [[ -z "$name" ]] && continue
  file="$DEST/$name"
  want="$(tr -d '\r' < "$file.sha256" 2> /dev/null | awk '{print $1}' | head -1)"
  have="$([[ -s "$file" ]] && sha "$file" || echo none)"
  header="$([[ -s "$file" ]] && head -c 21 "$file" || true)"
  if [[ -n "$want" && "$want" == "$have" && "$header" == "age-encryption.org/v1" ]]; then
    good=$((good + 1))
  else
    bad=$((bad + 1))
    echo "BAD      $name (checksum $([[ "$want" == "$have" ]] && echo ok || echo DIFFERENT), header $([[ "$header" == "age-encryption.org/v1" ]] && echo ok || echo WRONG))" >&2
  fi
done <<<"$wanted"
echo "== checked: $good good, $bad bad"

if [[ -n "$IDENTITY" ]]; then
  newest="$(tail -1 <<<"$wanted")"
  if age -d -i "$IDENTITY" "$DEST/$newest" 2> /dev/null | tar -tf - > "$DEST/.last-listing.txt" 2> /dev/null && [[ -s "$DEST/.last-listing.txt" ]]; then
    echo "OK       $newest opens with this computer's key: $(wc -l < "$DEST/.last-listing.txt" | tr -d ' ') items inside, for example: $(head -3 "$DEST/.last-listing.txt" | tr '\n' ' ')"
  else
    echo "PROBLEM  $newest does NOT open with $IDENTITY: this computer's key was not a recipient when it was made (use --since with a later time), or the key is the wrong one" >&2
    bad=$((bad + 1))
  fi
  rm -f "$DEST/.last-listing.txt"
fi

# keep the newest KEEP copies
total="$(ls -1 "$DEST" | grep -cE '^backup-[0-9]{8}T[0-9]{6}Z\.tar\.age$' || true)"
if [[ "$total" -gt "$KEEP" ]]; then
  ls -1 "$DEST" | grep -E '^backup-[0-9]{8}T[0-9]{6}Z\.tar\.age$' | sort | head -n "$((total - KEEP))" | while IFS= read -r old; do rm -f "$DEST/$old" "$DEST/$old.sha256"; done
  echo "== deleted $((total - KEEP)) old copies (keeping $KEEP)"
fi
echo "== $DEST holds $(ls -1 "$DEST" | grep -cE '^backup-[0-9]{8}T[0-9]{6}Z\.tar\.age$' || true) backups, $(du -sh "$DEST" | cut -f1) in all; newest: $(ls -1 "$DEST" | grep -E '^backup-.*\.tar\.age$' | sort | tail -1)"
[[ "$bad" -eq 0 ]]
