"""Command line for the platform tools: python -m l3mon <command>. Standard library only."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import os

from . import api_rules, config, deploy, hygiene, rehearsal, secrets_gen, story


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="l3mon", description="L3m0nCTF platform tools")
    sub = parser.add_subparsers(dest="cmd", required=True)

    h = sub.add_parser("hygiene", help="scan this repository for flags, secrets, dumps and story words")
    h.add_argument("--root", default=".", help="folder to scan (default: the current folder)")
    h.add_argument("--deny-words", default="", help="comma separated words that must not appear (story names)")
    h.add_argument("--deny-words-file", help="a file with one such word per line (kept outside the repository)")
    h.add_argument("--history", action="store_true", help="also read every line ever added in any commit (a secret deleted from the tree is still in the history)")

    c = sub.add_parser("config", help="check the event settings file and render what CTFd needs")
    csub = c.add_subparsers(dest="config_cmd", required=True)
    cv = csub.add_parser("validate", help="check a settings file")
    cv.add_argument("file")
    cr = csub.add_parser("render", help="write the generated deployment files")
    cr.add_argument("file")
    cr.add_argument("--out", required=True, help="folder for the generated files")

    s = sub.add_parser("secrets", help="generate the random secrets for this machine")
    ssub = s.add_subparsers(dest="secrets_cmd", required=True)
    sg = ssub.add_parser("generate", help="write one file per secret; values are never printed")
    sg.add_argument("--dir", default=".secrets", help="where to write them (default: .secrets)")
    mode = sg.add_mutually_exclusive_group()
    mode.add_argument("--force", action="store_true", help="replace secrets that already exist")
    mode.add_argument("--only-missing", action="store_true", help="write only the secrets whose file does not exist yet; change nothing else")

    a = sub.add_parser("api-rules", help="the nginx rules that keep CTFd's administrator API for the organisers' addresses")
    a.add_argument("action", choices=["generate", "check"], help="generate rewrites the two snippet files; check fails when they are out of date")
    a.add_argument("--image", default="l3mon/ctfd:dev", help="the platform image to read CTFd's routes from")

    d = sub.add_parser("deploy", help="what is missing before the production stack can start")
    dsub = d.add_subparsers(dest="deploy_cmd", required=True)
    dc = dsub.add_parser("check", help="read the event settings, the secrets, the TLS files and the organisers' addresses; exit 1 when anything is BLOCKED")
    dc.add_argument("--root", default=str(Path(__file__).resolve().parents[2]), help="the repository folder (default: the repository this tool is in, so it works from any folder)")
    dc.add_argument("--event", help="the event settings file (default: config/event.toml)")
    dc.add_argument("--secrets", help="the secrets folder (default: .secrets)")
    dc.add_argument("--tls", help="the folder with origin.pem, origin.key and cloudflare-origin-pull-ca.pem (default: $TLS_DIR or /etc/l3mon/tls)")
    dc.add_argument("--admin-nets", help="the organisers' addresses file (default: $ADMIN_NETS_FILE)")

    r = sub.add_parser("rehearsal", help="open the broadcast window for a rehearsal and close the player pages to everybody but the crew (docs/runbooks/15-rehearsal.md)")
    r.add_argument("action", choices=["status", "on", "off"])
    r.add_argument("--hours", type=int, default=6, help="how long the rehearsal window stays open (1 to 48; default 6)")
    r.add_argument("--event", help="the event settings file (default: config/event.toml)")
    r.add_argument("--gate", help="nginx's crew-only file (default: $CREW_ONLY_FILE, else deploy/nginx/snippets/crew-only.conf)")

    st = sub.add_parser("story", help="compile and check the cold-open comics (the private story folder) into the files the platform serves")
    ssub2 = st.add_subparsers(dest="story_cmd", required=True)
    sb = ssub2.add_parser("build", help="check every channel's script and picture and write one bundle per channel")
    sb.add_argument("src", help="the story folder (library/*.svg and channels/*.json)")
    sb.add_argument("out", nargs="?", help="where to write the bundles (omit with --check)")
    sb.add_argument("--check", action="store_true", help="only check; write nothing")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if args.cmd == "deploy":
        items = deploy.run_checks(
            Path(args.root), event_file=Path(args.event) if args.event else None, secrets_dir=Path(args.secrets) if args.secrets else None,
            tls_dir=Path(args.tls) if args.tls else None, admin_nets=Path(args.admin_nets) if args.admin_nets else None,
        )
        for item in items:
            print(item.human())
        blocked = [i for i in items if i.status == deploy.BLOCKED]
        print(f"{len(blocked)} blocked, {sum(1 for i in items if i.status == deploy.TODO)} to do, {sum(1 for i in items if i.status == deploy.OK)} in place")
        return 1 if blocked else 0

    if args.cmd == "rehearsal":
        root = Path(__file__).resolve().parents[2]
        event = Path(args.event) if args.event else root / "config" / "event.toml"
        gate = Path(args.gate or os.environ.get("CREW_ONLY_FILE") or root / "deploy" / "nginx" / "snippets" / "crew-only.conf")
        try:
            if args.action == "status":
                s = rehearsal.status(event, gate)
                print(("A rehearsal is on" if s["rehearsing"] else "No rehearsal is on (not on)") + f": the window is {s['start']} to {s['end']}; the player pages are {s['gate']}.")
            elif args.action == "on":
                s = rehearsal.turn_on(event, gate, args.hours)
                print(f"Rehearsal on for {s['hours']} hours: the window is {s['start']} to {s['end']} (UTC); the player pages are {s['gate']}.")
                print("Render the settings, reload nginx and restart the platform to apply it (tools/ops/rehearsal.sh does all of it).")
            else:
                s = rehearsal.turn_off(event, gate)
                print(f"Rehearsal off: the real window is put back ({s['start']} to {s['end']}) and the player pages are {s['gate']}.")
                print("Render the settings, reload nginx and restart the platform to apply it; then clear what the rehearsal left (docs/runbooks/15-rehearsal.md).")
        except (rehearsal.RehearsalError, OSError) as exc:
            print(f"error: {exc}")
            return 1
        return 0

    if args.cmd == "hygiene":
        words = [w for w in args.deny_words.split(",") if w.strip()]
        if args.deny_words_file:
            words += [w.strip() for w in Path(args.deny_words_file).read_text(encoding="utf-8").splitlines() if w.strip() and not w.startswith("#")]
        findings = hygiene.scan(Path(args.root), words)
        if args.history:
            findings += hygiene.scan_history(Path(args.root), words)
        for f in findings:
            print(f.human())
        print(f"{len(findings)} finding(s)")
        return 1 if findings else 0

    if args.cmd == "story":
        return story.main(args.src, args.out, check=args.check)

    if args.cmd == "api-rules":
        return api_rules.main([args.action, "--image", args.image])

    if args.cmd == "config":
        try:
            data = config.load(Path(args.file))
        except (OSError, ValueError) as exc:
            print(f"cannot read {args.file}: {exc}")
            return 1
        errors = config.validate(data)
        for e in errors:
            print(f"error: {e}")
        if errors:
            return 1
        if args.config_cmd == "validate":
            print("the settings file is valid")
            return 0
        written = config.render(data, Path(args.out))
        for name, path in written.items():
            print(f"wrote {name} to {path}")
        return 0

    if args.cmd == "secrets":
        try:
            made = secrets_gen.generate(Path(args.dir), force=args.force, only_missing=args.only_missing)
        except FileExistsError as exc:
            print(f"error: {exc}")
            return 1
        print(f"wrote {len(made)} secrets to {args.dir}:")
        for name in made:
            print(f"  {name}")
        return 0
    return 2


if __name__ == "__main__":
    sys.exit(main())
