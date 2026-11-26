"""A safety net under the beginner site's SQL labs (security review of 26 Nov 2026).

The Villain's Login lab and its practice quiz run a query that contains what a visitor typed, ON PURPOSE: that is the lesson (SQL injection), and it stays exactly as the author
wrote it. What the author's code does not have is a limit: a visitor can type a query that runs for minutes (a recursive query that never ends) or asks for a gigabyte
(`randomblob(1000000000)`), and a public site would be taken down for everybody by a few of them.

`install()` replaces `sqlite3.connect` with a function that gives every new connection (the labs make one per request, in memory, and throw it away) a time budget and a size
limit. The lesson still works, because the injection `' OR 1=1 --` costs nothing; a query that goes beyond the budget is stopped by SQLite and the lab answers with its own
"Database error" text, as for any other broken query. Nothing of the author's files is edited: serve.py calls `install()` before it imports the site.

Two more layers, from a second review of this file: a cap on ALL the memory SQLite may use in the process (`hard_heap_limit`: a query that builds a hundred megabytes
inside its half second gets "out of memory" instead of the container's), and tighter limits on one statement (a short LIKE pattern, a few compound terms, shallow
expressions, few opcodes, no ATTACH): the labs' own queries are a few dozen characters.

The container's own limits (256 MB, one CPU) remain the last line: they keep any harm inside the beginner container and away from the platform.
"""
import sqlite3
import time

BUDGET_SECONDS = 0.5  # a connection may compute for this long, counted from the moment it is made
MAX_LENGTH = 100_000  # the largest string or blob SQLite may build (the default is a billion bytes)
MAX_SQL_LENGTH = 10_000  # the largest statement (the labs cut what a visitor types at 200 and 500 characters)
CHECK_EVERY = 1000  # SQLite virtual machine instructions between two looks at the clock
HEAP_LIMIT = 64 * 1024 * 1024  # all of SQLite's memory in this process (PRAGMA hard_heap_limit is process-wide); the container has 256 MB
TIGHT = (  # (the sqlite3 constant, the value): far above what the labs need, far below the defaults
    ("SQLITE_LIMIT_LIKE_PATTERN_LENGTH", 100),  # default 50000
    ("SQLITE_LIMIT_COMPOUND_SELECT", 10),  # default 500
    ("SQLITE_LIMIT_EXPR_DEPTH", 100),  # default 1000
    ("SQLITE_LIMIT_VDBE_OP", 5000),  # default 250000 (the size of one compiled statement)
    ("SQLITE_LIMIT_ATTACHED", 0),  # a lab never opens a second database
)


def install():
    """Guard every connection made from now on. Safe to call twice."""
    if getattr(sqlite3.connect, "_l3mon_guarded", False):
        return
    original = sqlite3.connect
    try:  # process-wide, set once (SQLite 3.31 or newer; the image has 3.40): if it is not known the other limits still apply
        probe = original(":memory:")
        probe.execute(f"PRAGMA hard_heap_limit={HEAP_LIMIT}")
        probe.close()
    except sqlite3.Error:
        pass

    def guarded(*args, **kwargs):
        db = original(*args, **kwargs)
        db.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, MAX_LENGTH)
        db.setlimit(sqlite3.SQLITE_LIMIT_SQL_LENGTH, MAX_SQL_LENGTH)
        for name, value in TIGHT:
            db.setlimit(getattr(sqlite3, name), value)
        deadline = time.monotonic() + BUDGET_SECONDS
        db.set_progress_handler(lambda: 1 if time.monotonic() > deadline else 0, CHECK_EVERY)  # a non-zero answer stops the statement ("interrupted")
        return db

    guarded._l3mon_guarded = True
    guarded._l3mon_original = original
    sqlite3.connect = guarded


def uninstall():
    """Put the original back (for the tests)."""
    original = getattr(sqlite3.connect, "_l3mon_original", None)
    if original is not None:
        sqlite3.connect = original
        try:  # no heap cap any more (the tests share one process)
            probe = original(":memory:")
            probe.execute("PRAGMA hard_heap_limit=0")
            probe.close()
        except sqlite3.Error:
            pass
