"""The credentials database: one SQLite file, one table, and the database itself refuses a record of the wrong shape.

What each category shows is fixed by the organisers (the overview of 26 Nov 2026) and enforced here by CHECK constraints, so no tool or hand edit
can store, say, a winner without a rank or an organiser with a studio:

    ORG  Event Name "L3m0n CTF 2026"             Role "Organizer"    Name                                Issue Date
    PAR  Event Name "L3m0n CTF 2026 Qualifiers"  Role "Participant"  Username, Studio Name                Issue Date
    ACH  Event Name "L3m0n CTF 2026 Finals"      Role "Winner"       Username, Studio Name, Studio Rank 1-3   Issue Date
    APP  Event Name "L3m0n CTF 2026 Finals"      Role "Participant"  Username, Studio Name, Studio Rank 4+    Issue Date

`id`, `verify_code`, `source_ref` and the status fields are never shown as fields. A code is never deleted: it is `withdrawn` (with a private
reason) or `replaced` by a newer code of the same person (a qualifier who reached the finals keeps one code, the finals one).
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

from . import codes

SCHEMA_VERSION = "1"

EVENT = {"ORG": "L3m0n CTF 2026", "PAR": "L3m0n CTF 2026 Qualifiers", "ACH": "L3m0n CTF 2026 Finals", "APP": "L3m0n CTF 2026 Finals"}
ROLE = {"ORG": "Organizer", "PAR": "Participant", "ACH": "Winner", "APP": "Participant"}

SCHEMA = f"""
CREATE TABLE IF NOT EXISTS meta (
  key   TEXT PRIMARY KEY,
  value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS credentials (
  id               INTEGER PRIMARY KEY,
  verify_code      TEXT NOT NULL UNIQUE,
  category         TEXT NOT NULL CHECK (category IN ('ORG','PAR','ACH','APP')),
  event_name       TEXT NOT NULL,
  role             TEXT NOT NULL,
  name             TEXT,
  username         TEXT,
  studio_name      TEXT,
  studio_rank      INTEGER,
  issue_date       TEXT NOT NULL CHECK (issue_date GLOB '[0-9][0-9][0-9][0-9]-[0-1][0-9]-[0-3][0-9]' AND date(issue_date) IS issue_date),
  status           TEXT NOT NULL DEFAULT 'valid' CHECK (status IN ('valid','withdrawn','replaced')),
  withdrawn_reason TEXT,
  replaced_by      TEXT REFERENCES credentials(verify_code),
  source_ref       TEXT NOT NULL,
  CHECK (verify_code GLOB '{codes.PREFIX}-' || category || '-[{codes.ALPHABET}][{codes.ALPHABET}][{codes.ALPHABET}][{codes.ALPHABET}][{codes.ALPHABET}]'),
  CHECK ((status = 'replaced') = (replaced_by IS NOT NULL)),
  CHECK ((status = 'withdrawn') = (withdrawn_reason IS NOT NULL)),
  -- coalesce(..., 0): in SQL a test on a missing value is "unknown", and a CHECK lets "unknown" pass; here it counts as a failure
  CHECK (coalesce(
    (category = 'ORG' AND event_name = '{EVENT["ORG"]}' AND role = '{ROLE["ORG"]}'
       AND trim(name, ' ' || char(9, 10, 13)) <> '' AND username IS NULL AND studio_name IS NULL AND studio_rank IS NULL) OR
    (category = 'PAR' AND event_name = '{EVENT["PAR"]}' AND role = '{ROLE["PAR"]}'
       AND name IS NULL AND trim(username, ' ' || char(9, 10, 13)) <> '' AND trim(studio_name, ' ' || char(9, 10, 13)) <> '' AND studio_rank IS NULL) OR
    (category = 'ACH' AND event_name = '{EVENT["ACH"]}' AND role = '{ROLE["ACH"]}'
       AND name IS NULL AND trim(username, ' ' || char(9, 10, 13)) <> '' AND trim(studio_name, ' ' || char(9, 10, 13)) <> '' AND typeof(studio_rank) = 'integer' AND studio_rank BETWEEN 1 AND 3) OR
    (category = 'APP' AND event_name = '{EVENT["APP"]}' AND role = '{ROLE["APP"]}'
       AND name IS NULL AND trim(username, ' ' || char(9, 10, 13)) <> '' AND trim(studio_name, ' ' || char(9, 10, 13)) <> '' AND typeof(studio_rank) = 'integer' AND studio_rank >= 4),
    0)
  )
);
-- the five letters are unique across every category
-- one VALID code per person and category (a withdrawn code may be re-issued with --reissue)
CREATE UNIQUE INDEX IF NOT EXISTS credentials_person ON credentials (category, source_ref) WHERE status = 'valid';
CREATE UNIQUE INDEX IF NOT EXISTS credentials_suffix ON credentials (substr(verify_code, {len(codes.PREFIX) + 6}));
"""


class StoreError(Exception):
    pass


@dataclass
class Credential:
    verify_code: str
    category: str
    issue_date: str
    source_ref: str
    name: str | None = None
    username: str | None = None
    studio_name: str | None = None
    studio_rank: int | None = None
    status: str = "valid"
    withdrawn_reason: str | None = None
    replaced_by: str | None = None
    id: int | None = None
    event_name: str = field(default="")
    role: str = field(default="")

    def __post_init__(self):
        self.event_name = self.event_name or EVENT.get(self.category, "")
        self.role = self.role or ROLE.get(self.category, "")

    @property
    def suffix(self) -> str:
        return self.verify_code.rsplit("-", 1)[-1]

    def shown(self) -> list[tuple[str, str]]:
        """The fields a visitor sees, in the agreed order (labels exactly as in the overview)."""
        out = [("Event Name", self.event_name), ("Role", self.role)]
        if self.category == "ORG":
            out.append(("Name", self.name or ""))
        else:
            out += [("Username", self.username or ""), ("Studio Name", self.studio_name or "")]
            if self.category in ("ACH", "APP"):
                out.append(("Studio Rank", codes.ordinal(self.studio_rank)))
        out.append(("Issue Date", self.issue_date))
        return out


COLUMNS = ("id", "verify_code", "category", "event_name", "role", "name", "username", "studio_name", "studio_rank", "issue_date",
           "status", "withdrawn_reason", "replaced_by", "source_ref")


def connect(path: Path, create: bool = False) -> sqlite3.Connection:
    path = Path(path)
    if not create and not path.is_file():
        raise StoreError(f"no credentials database at {path} (make one with: python -m l3mon verify init {path})")
    conn = sqlite3.connect(str(path))
    conn.isolation_level = None  # autocommit; a multi-record change opens its own BEGIN ... COMMIT (issue.issue)
    conn.execute("PRAGMA foreign_keys = ON")
    if create:
        conn.executescript(SCHEMA)
        conn.execute("INSERT OR IGNORE INTO meta (key, value) VALUES ('schema', ?)", (SCHEMA_VERSION,))
        conn.commit()
    version = conn.execute("SELECT value FROM meta WHERE key = 'schema'").fetchone()
    if not version or version[0] != SCHEMA_VERSION:
        conn.close()
        raise StoreError(f"{path} is not a credentials database of schema {SCHEMA_VERSION}")
    return conn


def all_rows(conn: sqlite3.Connection) -> list[Credential]:
    cur = conn.execute(f"SELECT {', '.join(COLUMNS)} FROM credentials ORDER BY id")
    return [Credential(**dict(zip(COLUMNS, row))) for row in cur.fetchall()]


def get(conn: sqlite3.Connection, code: str) -> Credential | None:
    row = conn.execute(f"SELECT {', '.join(COLUMNS)} FROM credentials WHERE verify_code = ?", (code,)).fetchone()
    return Credential(**dict(zip(COLUMNS, row))) if row else None


def insert(conn: sqlite3.Connection, cred: Credential) -> Credential:
    values = {c: getattr(cred, c) for c in COLUMNS if c != "id"}
    try:
        cur = conn.execute(f"INSERT INTO credentials ({', '.join(values)}) VALUES ({', '.join('?' for _ in values)})", tuple(values.values()))
    except sqlite3.IntegrityError as exc:
        raise StoreError(f"refused by the database ({exc}): {cred.category} {cred.source_ref}") from exc
    cred.id = cur.lastrowid
    return cred


def withdraw(conn: sqlite3.Connection, code: str, reason: str) -> Credential:
    reason = (reason or "").strip()
    if not reason:
        raise StoreError("a withdrawal needs a reason (it is kept privately, never shown)")
    cred = get(conn, code)
    if cred is None:
        raise StoreError(f"no such code: {code}")
    if cred.status != "valid":
        raise StoreError(f"{code} is already {cred.status}")
    conn.execute("UPDATE credentials SET status = 'withdrawn', withdrawn_reason = ? WHERE verify_code = ?", (reason, code))
    return get(conn, code)


def replace(conn: sqlite3.Connection, old: str, new: str) -> None:
    conn.execute("UPDATE credentials SET status = 'replaced', replaced_by = ? WHERE verify_code = ? AND status = 'valid'", (new, old))
