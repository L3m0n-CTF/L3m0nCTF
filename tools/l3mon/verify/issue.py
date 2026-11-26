"""Issue codes from the crew's inputs: `python -m l3mon verify issue DB --date YYYY-MM-DD [--org FILE] [--prelims CSV] [--finals CSV] [--reissue CODE]`.

Inputs (all PRIVATE files, kept outside this repository):

    --org FILE       one organiser's full name per line (blank lines and lines starting with # are ignored)
    --prelims CSV    the qualifier participants, as export_prelims.py writes it: columns user_id, username, studio
                     (only members of studios that scored above 0 TRP; the export applies that rule)
    --finals CSV     the finals results: columns username, studio, rank, and optionally user_id (the platform's user id). Rank 1-3 -> ACH (Winner),
                     4 and up -> APP; two studios cannot share a rank. PROVISIONAL: the finals system's export format is not decided yet; this is the
                     shape the tool expects until then.

Who is who: a player is recognised by EITHER key, the platform user id or the username (compared after Unicode NFKC normalisation, with runs of
spaces made one and case ignored). Organisers are recognised by their name the same way. When the two keys of one row point at two different
people already in the database, the run stops and says so instead of guessing.

Rules:
  * One valid code per person. A person keeps the code they already have: a re-run issues codes only to people who have none, so it is safe and a
    certificate never changes its code. The date given is the Issue Date of the codes issued in THIS run.
  * A qualifier who reaches the finals ends with the finals code: issuing it marks their PAR code `replaced` (its page links to the new one), and a
    qualifier list read after the finals gives nothing to someone who already has a finals code.
  * A withdrawn code is NOT replaced by a routine re-run (a code withdrawn for cheating stays withdrawn). To give its holder a new code, for example
    after correcting a rank, withdraw the old code and run issue with `--reissue <old code>`.
  * Nothing is ever deleted. Someone in the database but no longer in an input is reported, not removed (use `withdraw`).
  * All or nothing: any refusal stops the run and nothing is written.
"""
from __future__ import annotations

import csv
import io
import re
import sqlite3
import unicodedata
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from . import codes, store


class IssueError(Exception):
    pass


@dataclass
class Report:
    issued: dict = field(default_factory=lambda: {c: 0 for c in codes.CATEGORIES})
    kept: dict = field(default_factory=lambda: {c: 0 for c in codes.CATEGORIES})
    replaced: int = 0
    skipped_finalists: int = 0
    held_withdrawn: int = 0
    warnings: list = field(default_factory=list)

    def lines(self) -> list[str]:
        out = [f"{c}: {self.issued[c]} issued, {self.kept[c]} already had a code" for c in codes.CATEGORIES]
        if self.replaced:
            out.append(f"{self.replaced} qualifier code(s) replaced by the holder's finals code")
        if self.skipped_finalists:
            out.append(f"{self.skipped_finalists} qualifier(s) skipped: they already have a finals code")
        if self.held_withdrawn:
            out.append(f"{self.held_withdrawn} person(s) skipped: their code was withdrawn (use --reissue <code> to give them a new one)")
        out += [f"warning: {w}" for w in self.warnings]
        return out


def _tidy(text: str) -> str:
    """A name the crew typed: ends trimmed, runs of spaces made one."""
    return re.sub(r"\s+", " ", (text or "").strip())


def _key(text: str) -> str:
    """What makes two spellings the same person: Unicode NFKC, runs of spaces made one, case ignored."""
    return _tidy(unicodedata.normalize("NFKC", text or "")).casefold()


def read_org(path: Path) -> list[str]:
    names, seen = [], set()
    for line in Path(path).read_text(encoding="utf-8-sig").splitlines():
        name = _tidy(line)
        if not name or name.startswith("#"):
            continue
        if _key(name) in seen:
            raise IssueError(f"{path}: {name!r} is listed twice")
        seen.add(_key(name))
        names.append(name)
    return names


BEGIN, END = "-----BEGIN L3MON PRELIMS EXPORT-----", "-----END L3MON PRELIMS EXPORT-----"


def _read_csv(path: Path, required: tuple[str, ...]) -> list[dict]:
    text = Path(path).read_text(encoding="utf-8-sig")
    if BEGIN in text:  # the raw output of export_prelims.py: keep only the part between its markers
        if END not in text:
            raise IssueError(f"{path}: the export is cut short (no end marker)")
        text = text.split(BEGIN, 1)[1].split(END, 1)[0].lstrip("\r\n")
    reader = csv.DictReader(io.StringIO(text, newline=""))
    header = [h.strip().lower() for h in (reader.fieldnames or [])]
    missing = [c for c in required if c not in header]
    if missing:
        raise IssueError(f"{path}: missing column(s) {', '.join(missing)} (found: {', '.join(header) or 'none'})")
    rows = []
    for n, raw in enumerate(reader, start=2):
        # values are stored as the platform has them: only the ends are trimmed (a double space inside a name is part of the name)
        row = {k.strip().lower(): (v or "").strip() for k, v in raw.items() if k}
        if not any(row.values()):
            continue
        for c in required:
            if not row.get(c):
                raise IssueError(f"{path} line {n}: empty {c}")
        row["_line"] = n
        rows.append(row)
    return rows


def read_prelims(path: Path) -> list[dict]:
    rows = _read_csv(path, ("user_id", "username", "studio"))
    ids, names = set(), set()
    for r in rows:
        if not r["user_id"].isdigit():
            raise IssueError(f"{path} line {r['_line']}: user_id must be the platform's numeric user id, got {r['user_id']!r}")
        if r["user_id"] in ids or _key(r["username"]) in names:
            raise IssueError(f"{path} line {r['_line']}: user {r['user_id']} ({r['username']!r}) is listed twice")
        ids.add(r["user_id"])
        names.add(_key(r["username"]))
    return rows


def read_finals(path: Path) -> list[dict]:
    rows = _read_csv(path, ("username", "studio", "rank"))
    ids, names, studio_rank, rank_studio = set(), set(), {}, {}
    for r in rows:
        if not re.fullmatch(r"[1-9][0-9]{0,3}", r["rank"]):
            raise IssueError(f"{path} line {r['_line']}: rank must be a whole number from 1, got {r['rank']!r}")
        r["rank"] = int(r["rank"])
        if r.get("user_id") and not r["user_id"].isdigit():
            raise IssueError(f"{path} line {r['_line']}: user_id must be numeric when given, got {r['user_id']!r}")
        if (r.get("user_id") and r["user_id"] in ids) or _key(r["username"]) in names:
            raise IssueError(f"{path} line {r['_line']}: {r['username']!r} is listed twice")
        if r.get("user_id"):
            ids.add(r["user_id"])
        names.add(_key(r["username"]))
        studio = _key(r["studio"])
        if studio_rank.setdefault(studio, r["rank"]) != r["rank"]:
            raise IssueError(f"{path} line {r['_line']}: studio {r['studio']!r} has two different ranks")
        if rank_studio.setdefault(r["rank"], studio) != studio:
            raise IssueError(f"{path} line {r['_line']}: rank {r['rank']} is given to two studios (only the top three studios are winners)")
    return rows


def _check_date(text: str) -> str:
    try:
        d = date.fromisoformat(text)
    except (TypeError, ValueError):
        raise IssueError(f"--date must be a date like 2026-11-30, got {text!r}") from None
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
        raise IssueError(f"--date must be written YYYY-MM-DD, got {text!r}")
    return d.isoformat()


# ------------------------------------------------------------------ who is who

def _keys_of(c: store.Credential) -> set[tuple[str, str]]:
    if c.category == "ORG":
        return {("org", _key(c.name))}
    keys = {("name", _key(c.username))}
    m = re.search(r":user:(\d+)\Z", c.source_ref)
    if m:
        keys.add(("id", m.group(1)))
    return keys


def _row_keys(row: dict) -> set[tuple[str, str]]:
    keys = {("name", _key(row["username"]))}
    if row.get("user_id"):
        keys.add(("id", row["user_id"]))
    return keys


class _People:
    """The database's codes indexed by every key of their holder."""

    def __init__(self, rows: list[store.Credential]):
        self.rows = list(rows)

    def add(self, c: store.Credential) -> None:
        self.rows.append(c)

    def find(self, keys: set, categories: tuple[str, ...], status: str) -> list[store.Credential]:
        seen, out = set(), []
        for c in self.rows:
            if c.category in categories and c.status == status and c.verify_code not in seen and _keys_of(c) & keys:
                seen.add(c.verify_code)
                out.append(c)
        return out

    def one(self, keys: set, categories: tuple[str, ...], status: str, who: str) -> store.Credential | None:
        found = self.find(keys, categories, status)
        if len(found) > 1:
            raise IssueError(f"{who} matches {len(found)} different {status} codes ({', '.join(c.verify_code for c in found)}): "
                             "the user id and the username point at different people. Fix the input; nothing was written.")
        return found[0] if found else None


def issue(conn: sqlite3.Connection, *, issue_date: str, org: list[str] | None = None, prelims: list[dict] | None = None,
          finals: list[dict] | None = None, reissue: set[str] | None = None, rand=None) -> Report:
    """Issue codes in one transaction (committed only when everything passed)."""
    issue_date = _check_date(issue_date)
    report = Report()
    people = _People(store.all_rows(conn))
    taken = {c.suffix for c in people.rows}
    pick = {"rand": rand} if rand else {}
    reissue = set(reissue or ())
    for code in reissue:
        c = next((c for c in people.rows if c.verify_code == code), None)
        if c is None or c.status != "withdrawn":
            raise IssueError(f"--reissue {code}: only a withdrawn code can be re-issued (withdraw it first)")
    used_reissue: set[str] = set()

    def new_code(category: str) -> str:
        suffix = codes.new_suffix(taken, **pick)
        taken.add(suffix)
        return codes.make(category, suffix)

    def blocked_by_withdrawal(keys: set, categories: tuple[str, ...]) -> bool:
        withdrawn = people.find(keys, categories, "withdrawn")
        if not withdrawn:
            return False
        if any(c.verify_code in reissue for c in withdrawn):
            used_reissue.update(c.verify_code for c in withdrawn if c.verify_code in reissue)
            return False
        report.held_withdrawn += 1
        return True

    def put(cred: store.Credential) -> store.Credential:
        cred = store.insert(conn, cred)
        people.add(cred)
        report.issued[cred.category] += 1
        return cred

    try:
        conn.execute("BEGIN")
        if org is not None:
            listed = set()
            for name in org:
                keys = {("org", _key(name))}
                listed |= keys
                if people.one(keys, ("ORG",), "valid", repr(name)):
                    report.kept["ORG"] += 1
                elif not blocked_by_withdrawal(keys, ("ORG",)):
                    put(store.Credential(verify_code=new_code("ORG"), category="ORG", issue_date=issue_date, source_ref="org:" + _key(name), name=name))
            gone = [c for c in people.rows if c.category == "ORG" and c.status == "valid" and not (_keys_of(c) & listed)]
            if gone:
                report.warnings.append(f"{len(gone)} organiser(s) have a code but are not in this list (not removed; use withdraw if meant)")

        if finals is not None:
            listed = set()
            for r in finals:
                keys = _row_keys(r)
                listed |= keys
                who = repr(r["username"])
                category = "ACH" if r["rank"] <= 3 else "APP"
                held = people.one(keys, ("ACH", "APP"), "valid", who)
                if held:
                    report.kept[held.category] += 1
                    if held.studio_rank != r["rank"]:
                        report.warnings.append(f"{who} already holds {held.verify_code} (rank {held.studio_rank}); the finals list now says rank {r['rank']}: "
                                               "not changed (withdraw it, then run again with --reissue to correct it)")
                    continue
                par = people.one(keys, ("PAR",), "valid", who)
                if blocked_by_withdrawal(keys, ("ACH", "APP")):
                    continue
                ref = f"finals:user:{r['user_id']}" if r.get("user_id") else f"finals:name:{_key(r['username'])}"
                cred = put(store.Credential(verify_code=new_code(category), category=category, issue_date=issue_date, source_ref=ref,
                                            username=r["username"], studio_name=r["studio"], studio_rank=r["rank"]))
                if par:
                    store.replace(conn, par.verify_code, cred.verify_code)
                    par.status, par.replaced_by = "replaced", cred.verify_code
                    report.replaced += 1
            gone = [c for c in people.rows if c.category in ("ACH", "APP") and c.status == "valid" and not (_keys_of(c) & listed)]
            if gone:
                report.warnings.append(f"{len(gone)} finalist(s) have a code but are not in this finals list (not removed)")

        if prelims is not None:
            listed = set()
            for r in prelims:
                keys = _row_keys(r)
                listed |= keys
                who = repr(r["username"])
                if people.find(keys, ("ACH", "APP"), "valid"):
                    report.skipped_finalists += 1
                    continue
                if people.one(keys, ("PAR",), "valid", who):
                    report.kept["PAR"] += 1
                    continue
                if people.find(keys, ("PAR",), "replaced"):
                    report.skipped_finalists += 1  # their qualifier code was replaced by a finals code (now withdrawn): nothing new here
                    continue
                if blocked_by_withdrawal(keys, ("PAR",)):
                    continue
                put(store.Credential(verify_code=new_code("PAR"), category="PAR", issue_date=issue_date, source_ref=f"prelims:user:{r['user_id']}",
                                     username=r["username"], studio_name=r["studio"]))
            gone = [c for c in people.rows if c.category == "PAR" and c.status == "valid" and not (_keys_of(c) & listed)]
            if gone:
                report.warnings.append(f"{len(gone)} qualifier(s) have a code but are not in this export (not removed)")

        unused = reissue - used_reissue
        if unused:
            raise IssueError(f"--reissue {', '.join(sorted(unused))}: the holder is not in the lists given to this run")
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    return report
