"""deploy/beginner/guard.py: the time and size limit under the beginner site's SQL labs (security review of 26 Nov 2026).

Pinned: the lesson still works (the injection `' OR 1=1 --` returns a row, an honest query returns nothing); a query that never ends is stopped within a fraction of a second; a query
that asks for a gigabyte is refused; the limits apply to every new connection and to nothing already made; installing twice does not stack; and the original can be put back.
"""
import importlib.util
import sqlite3
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def load_guard():
    spec = importlib.util.spec_from_file_location("beginner_guard", ROOT / "deploy" / "beginner" / "guard.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


needs_setlimit = pytest.mark.skipif(sys.version_info < (3, 11), reason="Connection.setlimit needs Python 3.11 (the image is 3.11)")


@pytest.fixture
def guard():
    module = load_guard()
    module.uninstall()
    original = sqlite3.connect
    module.install()
    yield module
    module.uninstall()
    sqlite3.connect = original


def lab_db():
    db = sqlite3.connect(":memory:")
    db.executescript("CREATE TABLE users (id INTEGER PRIMARY KEY, username TEXT, password TEXT); INSERT INTO users (username, password) VALUES ('dr', 'secret-1'), ('hank', 'secret-2');")
    return db


@needs_setlimit
def test_the_lesson_still_works_an_injection_returns_a_row_and_an_honest_query_does_not(guard):
    db = lab_db()
    injected = f"SELECT * FROM users WHERE username = '{chr(39)} OR 1=1 --' AND password = 'x'"
    assert db.execute(injected).fetchone() is not None
    honest = "SELECT * FROM users WHERE username = 'nobody' AND password = 'x'"
    assert db.execute(honest).fetchone() is None
    db.close()


@needs_setlimit
def test_a_query_that_never_ends_is_stopped_within_the_budget(guard):
    db = lab_db()
    runaway = "SELECT count(*) FROM (WITH RECURSIVE c(x) AS (SELECT 1 UNION ALL SELECT x+1 FROM c WHERE x < 1000000000000) SELECT x FROM c)"
    started = time.monotonic()
    with pytest.raises(sqlite3.OperationalError, match="interrupted"):
        db.execute(runaway).fetchall()
    assert time.monotonic() - started < guard.BUDGET_SECONDS + 1.5, "stopped soon after the budget, not after minutes"
    db.close()


@needs_setlimit
def test_a_query_that_asks_for_a_gigabyte_is_refused(guard):
    db = lab_db()
    with pytest.raises(sqlite3.DataError):
        db.execute("SELECT length(randomblob(1000000000))").fetchall()
    with pytest.raises(sqlite3.DataError):
        db.execute("SELECT length(zeroblob(1000000000))").fetchall()
    db.close()


@needs_setlimit
def test_a_query_that_builds_a_lot_of_memory_inside_its_budget_is_stopped_by_the_heap_cap(guard):
    """A hundred megabytes of rows to sort, built well inside the half second: the cap of 64 MB for all of SQLite answers `out of memory`, the container's 256 MB is never reached."""
    db = lab_db()
    heavy = "SELECT count(*) FROM (SELECT x, randomblob(50000) AS b FROM (WITH RECURSIVE c(x) AS (SELECT 1 UNION ALL SELECT x+1 FROM c WHERE x < 4000) SELECT x FROM c) ORDER BY b)"
    with pytest.raises((sqlite3.OperationalError, sqlite3.DataError, MemoryError)):
        db.execute(heavy).fetchall()
    db.close()


@needs_setlimit
@pytest.mark.parametrize("sql", [
    "SELECT 1 WHERE 'a' LIKE '" + "%a" * 60 + "'",  # a LIKE pattern of 120 characters
    "SELECT 1 UNION ALL " + " UNION ALL ".join(["SELECT 1"] * 12),  # 13 compound terms
    "ATTACH DATABASE ':memory:' AS other",  # a second database
])
def test_one_statement_may_not_be_a_pathological_one(guard, sql):
    db = lab_db()
    with pytest.raises(sqlite3.Error):
        db.execute(sql).fetchall()
    db.close()


@needs_setlimit
def test_a_statement_that_is_too_long_is_refused(guard):
    db = lab_db()
    with pytest.raises(sqlite3.DataError):
        db.execute("SELECT 1 WHERE 1=1" + " AND 1=1" * 5000)
    db.close()


@needs_setlimit
def test_every_new_connection_gets_its_own_fresh_budget(guard):
    first = lab_db()
    time.sleep(guard.BUDGET_SECONDS + 0.3)
    second = lab_db()  # made later: its clock starts at its own birth
    assert second.execute("SELECT count(*) FROM users").fetchone() == (2,)
    first.close()
    second.close()


@needs_setlimit
def test_installing_twice_does_not_stack_and_uninstall_puts_the_original_back(guard):
    guarded = sqlite3.connect
    guard.install()
    assert sqlite3.connect is guarded, "a second install changes nothing"
    guard.uninstall()
    assert getattr(sqlite3.connect, "_l3mon_guarded", False) is False
    guard.install()
    assert getattr(sqlite3.connect, "_l3mon_guarded", False) is True


def test_the_wrapper_installs_the_net_before_it_imports_the_site():
    serve = (ROOT / "deploy" / "beginner" / "serve.py").read_text(encoding="utf-8")
    assert serve.index("guard.install()") < serve.index("from app import app"), "the site's own import of sqlite3 must find the guarded connect"
