#!/usr/bin/env bash
# tools/ops/beginner.sh - the Beginners Section on the platform (docs/runbooks/21-beginner-section.md).
#
#   bash tools/ops/beginner.sh pack <folder> <out.tgz>
#       Make the source archive the image is built from. <folder> is the Beginners Section (the one with app.py). The archive holds ONLY app.py, challenges.py, templates/ and
#       static/ of the author (copied by name: nothing else in the folder can get in) and our two files serve.py and guard.py (deploy/beginner/: a time and size limit under
#       the SQL labs, so that nobody can take the site down with one heavy query). It is checked once more before it is kept: no flags export, no tests, no build leftovers, no
#       compiled files. It refuses to overwrite a file. challenges.py and the downloads hold the flags (a lesson needs them): the archive is as secret as the flags are.
#
#   bash tools/ops/beginner.sh import [--apply] <flags.json>
#       Load the 15 challenges into the platform as channel 0, all OFF AIR (plugins/l3mon_release/beginner.py). Without --apply it only says what it would do. The flags
#       file is copied to the platform VM with mode 0600 for the length of the call and deleted at once; its content is never printed or logged.
#
# It needs, on this computer: bash (Git Bash on Windows), tar, and for `import` gcloud signed in with the project's configuration and CLOUDSDK_CORE_PROJECT set.
# Overrides: L3MON_GCLOUD, L3MON_GCLOUD_CONFIG (l3mon-new), L3MON_ZONE (asia-south1-a), L3MON_APP_VM (l3mon-app).
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
GCLOUD="${L3MON_GCLOUD:-gcloud}"
CONFIG="${L3MON_GCLOUD_CONFIG:-l3mon-new}"
ZONE="${L3MON_ZONE:-asia-south1-a}"
APP="${L3MON_APP_VM:-l3mon-app}"

usage() { sed -n '2,/^set -euo pipefail/p' "${BASH_SOURCE[0]}" | sed '$d' | sed 's/^# \{0,1\}//' >&2; exit 2; }

ACTION="${1:-}"
case "$ACTION" in
  pack)
    FOLDER="${2:-}" OUT="${3:-}"
    [[ -n "$FOLDER" && -n "$OUT" && "$OUT" == *.tgz ]] || usage
    for need in app.py challenges.py templates static; do
      [[ -e "$FOLDER/$need" ]] || { echo "this is not the Beginners Section: $FOLDER/$need is missing" >&2; exit 2; }
    done
    [[ ! -e "$OUT" ]] || { echo "refusing to overwrite $OUT" >&2; exit 2; }
    # a staging folder: the author's four things by name, plus our two files; then tar from there (through a redirect, not a file name: GNU tar on Windows reads
    # `C:/...` as a remote host)
    stage="$(mktemp -d)"
    trap 'rm -rf "$stage"' EXIT
    # (tar, not cp: on Windows cp treats `strings_attached` and `strings_attached.exe` as one file and fails)
    tar -cf - --exclude='__pycache__' --exclude='*.pyc' -C "$FOLDER" app.py challenges.py templates static | tar -xf - -C "$stage"
    cp "$HERE/deploy/beginner/serve.py" "$HERE/deploy/beginner/guard.py" "$stage/"
    tar -czf - --exclude='__pycache__' --exclude='*.pyc' -C "$stage" app.py challenges.py serve.py guard.py templates static > "$OUT"
    # belt and braces: whatever happened above, none of these may be in the archive
    if tar -tzf - < "$OUT" | grep -E '(^|/)(flags|tests|build|__pycache__|\.git|\.env)(/|$)|\.pyc$|beginner_flags|requirements\.txt|build_files|export_flags'; then
      rm -f "$OUT"
      echo "REFUSED: the archive would hold something that must stay out (listed above); it was deleted" >&2
      exit 1
    fi
    sum="$(if command -v sha256sum > /dev/null; then sha256sum "$OUT"; else shasum -a 256 "$OUT"; fi | cut -d' ' -f1)"
    echo "packed $OUT: $(tar -tzf - < "$OUT" | grep -vc '/$') files, $(wc -c < "$OUT" | tr -d ' ') bytes, sha256 $sum"
    echo "the archive holds the flags (challenges.py and the practice files): keep it out of every repository and delete it after the build"
    ;;

  import)
    shift
    APPLY=""
    if [[ "${1:-}" == "--apply" ]]; then APPLY="--apply"; shift; fi
    FILE="${1:-}"
    [[ -n "$FILE" && -f "$FILE" ]] || usage
    : "${CLOUDSDK_CORE_PROJECT:?set CLOUDSDK_CORE_PROJECT to the Google Cloud project id first (PowerShell: \$env:CLOUDSDK_CORE_PROJECT = \"<project id>\")}"
    g() { "$GCLOUD" --configuration="$CONFIG" "$@"; }
    g compute scp --zone="$ZONE" --tunnel-through-iap --quiet "$FILE" "$APP:beginner_flags.json"
    # the file is private from the moment it is there, is used once, and is removed whatever the import says
    g compute ssh "$APP" --zone="$ZONE" --tunnel-through-iap --quiet --command \
      "chmod 600 ~/beginner_flags.json; cd ~/L3m0nCTF && docker exec -i \$(tools/compose.sh ps -q ctfd) l3mon-run /opt/venv/bin/python -c 'from CTFd.plugins.l3mon_release.beginner import main; main()' $APPLY < ~/beginner_flags.json 2>&1 | grep -vE 'Loaded module|DeprecationWarning|LegacyAPIWarning|alembic|^ \\* '; code=\${PIPESTATUS[0]}; rm -f ~/beginner_flags.json; exit \$code"
    ;;

  *) usage ;;
esac
