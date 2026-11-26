#!/usr/bin/env bash
# Publish a built verification website into the folder nginx serves (docs/runbooks/21-verify-portal.md). Run ON the server, after the owner's yes.
#
#   bash tools/ops/verify-publish.sh /tmp/verify-site.tgz            # the folder is $L3MON_VERIFY_SITE_DIR (from /etc/l3mon/production.env)
#   bash tools/ops/verify-publish.sh /tmp/verify-site.tgz /some/dir  # or name it
#
# The archive is the output folder of `python -m l3mon verify build`, packed with: tar -czf verify-site.tgz -C <out> .
# nginx sees the folder through a Docker bind mount, which follows the folder itself, so the folder is never replaced: the new files are copied
# INTO it and then the files the new build no longer has are removed. nginx needs no reload. Nothing is printed but counts.
set -euo pipefail

archive="${1:?usage: verify-publish.sh <archive.tgz> [site folder]}"
if [ -z "${2:-}" ] && [ -z "${L3MON_VERIFY_SITE_DIR:-}" ] && [ -r /etc/l3mon/production.env ]; then
  # shellcheck disable=SC1091
  set -a; . /etc/l3mon/production.env; set +a
fi
dest="${2:-${L3MON_VERIFY_SITE_DIR:-}}"
[ -n "$dest" ] || { echo "error: name the site folder or set L3MON_VERIFY_SITE_DIR" >&2; exit 1; }
[ -f "$archive" ] || { echo "error: no archive at $archive" >&2; exit 1; }
case "$dest" in /|/etc|/etc/l3mon|/home|"$HOME") echo "error: refusing to publish into $dest" >&2; exit 1;; esac

work="$(mktemp -d)"
trap 'rm -rf -- "$work"' EXIT
tar -xzf "$archive" -C "$work"
for f in build.json index.html 404.html robots.txt; do
  [ -f "$work/$f" ] || { echo "error: the archive is not a verification build (no $f)" >&2; exit 1; }
done
if find "$work" -mindepth 1 \( -type l -o -name '.*' \) | grep -q .; then
  echo "error: the archive holds a link or a hidden file: refused" >&2; exit 1
fi

mkdir -p "$dest"
# one canonical spelling of the folder (no trailing slash, no //, no symlink): every path below is built from it
dest="$(cd "$dest" && pwd -P)"
case "$dest" in /|/etc|/etc/l3mon|/home) echo "error: refusing to publish into $dest" >&2; exit 1;; esac
if [ -n "$(ls -A "$dest")" ] && [ ! -f "$dest/build.json" ]; then
  echo "error: $dest is not empty and holds no build.json: refusing to change it" >&2; exit 1
fi

# assets first (hashed names: the old pages keep working), then the pages, then the summary
mkdir -p "$dest/assets"
cp -R "$work/assets/." "$dest/assets/"
for item in "$work"/*; do
  name="$(basename "$item")"
  [ "$name" = assets ] || [ "$name" = build.json ] || cp -R "$item" "$dest/"
done
cp "$work/build.json" "$dest/build.json"

# remove what the new build no longer has (paths relative to the folder, as find itself prints them)
removed=0
while IFS= read -r -d '' rel; do
  case "$rel" in ''|/*|../*|*/../*) echo "error: unexpected path $rel" >&2; exit 1;; esac
  if [ ! -e "$work/$rel" ]; then rm -f -- "$dest/$rel"; removed=$((removed + 1)); fi
done < <(find "$dest" -type f -printf '%P\0')
find "$dest" -mindepth 1 -type d -empty -delete
chmod -R a+rX "$dest"
for f in build.json index.html 404.html; do
  [ -f "$dest/$f" ] || { echo "error: $dest/$f is missing after the publish: check the folder now" >&2; exit 1; }
done

pages="$(find "$dest" -name '*.html' -type f | wc -l | tr -d ' ')"
echo "Published into $dest: $pages pages, $removed old file(s) removed."
