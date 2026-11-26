"""The Beginners Section (26 Nov 2026, the owner's superior): 15 guided challenges that become channel 0 ("Test Card") of the platform, ALL OFF AIR.

The lessons, the practice files and the live labs are a small website of their own (beginner.l3m0nctf.xyz, deploy/beginner/). The platform holds what counts: one challenge per
episode with its flag and its points, and the task text with a link to the lesson. A player opens the link, learns, solves, and submits the flag here as any other flag.

    python -c "from CTFd.plugins.l3mon_release.beginner import main; main()" [--apply] < beginner_flags.json        (inside the application container: tools/ops/beginner.sh import)

Without --apply nothing is written: the plan is printed. With --apply: the challenges are made (hidden, a fixed value, a static case-sensitive flag), channel `test-card` and the 15
programmes are loaded through the crew's own plan code, and everything stays WITHHELD: nothing is announced and nothing is shown until the crew releases it.
What the input must be: the file `flags/beginner_flags.json` of the Beginners Section ({"challenges": [{slug, category, challenge, difficulty, points, flag, url, description}]}).

Rules this code keeps: a flag is never printed or logged, and never replaces a flag that is already stored; a challenge that already exists is not touched (so a second run changes
nothing); one wrong entry refuses the whole file before anything is written; the numbers 1 to 15 and the cells 0 to 14 are the order of the file.
"""
import json
import re
import sys

BASE_URL = "https://beginner.l3m0nctf.xyz"
CHANNEL = {
    "slug": "test-card",
    "name": "Test Card",
    "position": 0,
    "kind": "standard",
    "synopsis": "Never played a CTF? Start here: fifteen guided challenges, each with a short lesson.",
}
DIFFICULTY_WORDS = {"very easy": "warmup", "easy": "easy", "medium": "medium", "hard": "hard", "insane": "insane"}
FLAG = re.compile(r"^L3m0n\{[0-9a-f]{32}\}\Z")
SLUG = re.compile(r"^[a-z0-9][a-z0-9-]{0,47}\Z")
EXPECTED = 15
ATTRIBUTION = "L3m0nCTF beginner section"


class Refused(Exception):
    """The input is wrong; `problems` names every wrong entry, and nothing was written."""

    def __init__(self, problems):
        super().__init__("; ".join(problems))
        self.problems = problems


def read(raw):
    """The 15 entries from the file's text or its parsed form, checked completely. Raises Refused with every problem named."""
    try:
        data = json.loads(raw) if isinstance(raw, (str, bytes)) else raw
    except ValueError as error:
        raise Refused([f"the file is not JSON: {error}"]) from None
    items = data.get("challenges") if isinstance(data, dict) else None
    if not isinstance(items, list):
        raise Refused(['the file must be {"challenges": [...]}'])
    problems, entries, seen_slug, seen_flag = [], [], set(), set()
    if len(items) != EXPECTED:
        problems.append(f"expected {EXPECTED} challenges, the file has {len(items)}")
    for n, item in enumerate(items, 1):
        where = f"entry {n}"
        if not isinstance(item, dict):
            problems.append(f"{where}: not an object")
            continue
        slug = str(item.get("slug") or "")
        where = f"entry {n} ({slug or '?'})"
        title, category = str(item.get("challenge") or "").strip(), str(item.get("category") or "").strip()
        points, flag = item.get("points"), item.get("flag")
        difficulty = DIFFICULTY_WORDS.get(str(item.get("difficulty") or "").strip().lower())
        if not SLUG.match(slug):
            problems.append(f"{where}: the slug must be lowercase letters, digits and hyphens")
        elif slug in seen_slug:
            problems.append(f"{where}: the slug is used twice")
        seen_slug.add(slug)
        if not (1 <= len(title) <= 80):
            problems.append(f"{where}: the title must be 1 to 80 characters")
        if not (1 <= len(category) <= 80):
            problems.append(f"{where}: the category must be 1 to 80 characters")
        if not isinstance(points, int) or isinstance(points, bool) or not (1 <= points <= 1000):
            problems.append(f"{where}: points must be a whole number from 1 to 1000")
        if difficulty is None:
            problems.append(f"{where}: the difficulty '{item.get('difficulty')}' is not one of {sorted(DIFFICULTY_WORDS)}")
        if not isinstance(flag, str) or not FLAG.match(flag):
            problems.append(f"{where}: the flag does not have the shape of a platform flag")  # the value is never repeated here
        elif flag in seen_flag:
            problems.append(f"{where}: the same flag is used by another entry")
        else:
            seen_flag.add(flag)
        if item.get("url") != f"{BASE_URL}/c/{slug}":
            problems.append(f"{where}: the address must be {BASE_URL}/c/{slug}")
        tagline = str(item.get("description") or "").strip()
        if not tagline or len(tagline) > 300 or re.search(r"[<>]", tagline):
            problems.append(f"{where}: the description must be 1 to 300 characters of plain text")
        entries.append({"slug": slug, "title": title, "category": category, "points": points, "difficulty": difficulty, "flag": flag, "tagline": tagline})
    if problems:
        raise Refused(problems)
    return entries


def task_text(entry):
    """The task text as HTML (CTFd keeps it so; the player page keeps only paragraphs and an https link). It holds the link, never a flag."""
    link = f"{BASE_URL}/c/{entry['slug']}"
    tagline = entry["tagline"].replace("&", "&amp;")
    return (
        f"<p>{tagline}</p>"
        f"<p>This is a guided beginner challenge. Open the lesson, learn the idea, solve the challenge there, and bring the flag back here: "
        f'<a href="{link}">{link}</a></p>'
        "<p>The flag looks like L3m0n{...}</p>"
    )


def run(raw, apply=False, out=print):
    """Check the file, then (with apply) make what is missing. Returns a report; never contains a flag."""
    from CTFd.models import Challenges, Flags, db
    from CTFd.plugins.l3mon_core import audit
    from CTFd.plugins.l3mon_core.models import Programme
    from CTFd.plugins.l3mon_release import plan
    from CTFd.plugins.l3mon_release.reconcile import reconcile, serialize

    entries = read(raw)
    report = {"created": [], "kept": [], "flag_differs": [], "applied": bool(apply)}
    rows = {}
    for entry in entries:
        row = Challenges.query.filter_by(name=entry["title"], category=entry["category"]).first()
        rows[entry["slug"]] = row
        if row is None:
            report["created"].append(entry["slug"])
        else:
            report["kept"].append(entry["slug"])
            stored = Flags.query.filter_by(challenge_id=row.id).first()
            if stored is not None and stored.content != entry["flag"]:
                report["flag_differs"].append(entry["slug"])
    out(f"{len(entries)} challenges: {len(report['created'])} to create, {len(report['kept'])} already there ({len(report['flag_differs'])} with a different stored flag, left alone)")
    for n, entry in enumerate(entries, 1):
        state = "new" if entry["slug"] in report["created"] else "there"
        out(f"  {n:2}. {entry['slug']:22} {entry['difficulty']:7} {entry['points']:4} TRP  {entry['category']:20} [{state}]")
    if not apply:
        out("dry run: nothing was written (use --apply)")
        return report

    serialize()
    for entry in entries:
        if rows[entry["slug"]] is not None:
            continue
        row = Challenges(
            name=entry["title"], category=entry["category"], description=task_text(entry), value=entry["points"], state="hidden", type="standard", max_attempts=0,
            attribution=ATTRIBUTION,
        )
        db.session.add(row)
        db.session.flush()
        db.session.add(Flags(challenge_id=row.id, type="static", content=entry["flag"], data=""))  # data "" = case sensitive
        rows[entry["slug"]] = row
    db.session.commit()
    body = {
        "channels": [dict(CHANNEL)],
        "programmes": [
            {"challenge_id": rows[e["slug"]].id, "channel": CHANNEL["slug"], "cell": n, "number": n + 1, "slug": e["slug"], "difficulty": e["difficulty"], "delivery": "static_shared"}
            for n, e in enumerate(entries)
        ],
    }
    clean, problems = plan.validate_plan(body)
    if problems:
        db.session.rollback()
        raise Refused([f"the plan was refused: {dict(problems)}"])
    counts = plan.apply_plan(clean)
    audit.record("plan.sync", "plan", f"the Beginners Section: channel {CHANNEL['slug']} and {len(entries)} programmes, all off air")
    reconcile(locked=True)
    report["plan"] = counts
    out(f"applied: {json.dumps(counts)}")
    on_air = Programme.query.filter(Programme.release_state != "withheld").count()
    out(f"programmes not withheld after the import: {on_air} (must be 0)")
    report["not_withheld"] = on_air
    return report


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    apply = "--apply" in argv
    from CTFd import create_app

    app = create_app()
    with app.app_context():
        # the same "no announcement from a crew tool" rule as the demo round: nothing here ever tells players anything
        import CTFd.plugins.l3mon_release.reconcile as rc

        rc.announce = lambda *a, **k: None
        try:
            run(sys.stdin.read(), apply=apply)
        except Refused as error:
            print("REFUSED: nothing was written")
            for problem in error.problems:
                print("  -", problem)
            sys.exit(1)
    sys.exit(0)
