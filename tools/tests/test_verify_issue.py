"""Issuing codes (tools/l3mon/verify/issue.py): one code per person, re-runs are safe, finalists end with their finals code, all or nothing.

Every name here is made up: the real organiser list and exports are private and never enter this public repository.
"""
from __future__ import annotations

import pytest

from l3mon.verify import codes, issue, store

BEGIN, END = issue.BEGIN, issue.END


@pytest.fixture
def conn(tmp_path):
    c = store.connect(tmp_path / "c.sqlite", create=True)
    yield c
    c.close()


def write(tmp_path, name, text):
    p = tmp_path / name
    p.write_text(text, encoding="utf-8")
    return p


PRELIMS = "user_id,username,studio,prelims_rank\n11,ctf.username,Test,1\n12,neo.sample,Test,1\n13,trinity_x,Byte Bandits,2\n14,morpheus,Null Pointers,3\n"


def by_cat(conn):
    out: dict[str, list] = {c: [] for c in codes.CATEGORIES}
    for c in store.all_rows(conn):
        out[c.category].append(c)
    return out


# ------------------------------------------------------------------ reading the inputs

def test_the_organiser_list_ignores_blank_and_comment_lines_and_tidies_spaces(tmp_path):
    p = write(tmp_path, "org.txt", "﻿Jane  Doe\n\n# not a name\n  Alex Example  \n")
    assert issue.read_org(p) == ["Jane Doe", "Alex Example"]


def test_a_name_listed_twice_is_refused(tmp_path):
    with pytest.raises(issue.IssueError):
        issue.read_org(write(tmp_path, "org.txt", "Jane Doe\njane   doe\n"))


def test_the_raw_export_with_ctfds_own_start_up_lines_is_read_between_its_markers(tmp_path):
    raw = "Loaded module foo\nalembic says hi\n" + BEGIN + "\n" + PRELIMS + END + "\nmore noise\n"
    rows = issue.read_prelims(write(tmp_path, "export.txt", raw))
    assert [r["username"] for r in rows] == ["ctf.username", "neo.sample", "trinity_x", "morpheus"]


def test_a_cut_short_export_is_refused(tmp_path):
    with pytest.raises(issue.IssueError, match="cut short"):
        issue.read_prelims(write(tmp_path, "export.txt", BEGIN + "\n" + PRELIMS))


@pytest.mark.parametrize("text, msg", [
    ("username,studio\nx,y\n", "missing column"),
    ("user_id,username,studio\nabc,x,y\n", "numeric"),
    ("user_id,username,studio\n1,x,y\n1,z,y\n", "twice"),
    ("user_id,username,studio\n1,,y\n", "empty username"),
])
def test_a_bad_prelims_file_is_refused_with_the_line(tmp_path, text, msg):
    with pytest.raises(issue.IssueError, match=msg):
        issue.read_prelims(write(tmp_path, "p.csv", text))


@pytest.mark.parametrize("text, msg", [
    ("username,studio,rank\nx,y,0\n", "rank"),
    ("username,studio,rank\nx,y,first\n", "rank"),
    ("username,studio,rank\nx,Team,1\nz,team,2\n", "two different ranks"),
    ("username,studio,rank\nx,y,1\nX,y,1\n", "twice"),
    ("username,studio,rank,user_id\nx,y,1,abc\n", "numeric"),
])
def test_a_bad_finals_file_is_refused(tmp_path, text, msg):
    with pytest.raises(issue.IssueError, match=msg):
        issue.read_finals(write(tmp_path, "f.csv", text))


# ------------------------------------------------------------------ the rules

def test_everyone_gets_one_valid_code_of_the_right_category(conn, tmp_path):
    r = issue.issue(conn, issue_date="2026-11-30", org=["Jane Doe", "Alex Example"], prelims=issue.read_prelims(write(tmp_path, "p.csv", PRELIMS)))
    cats = by_cat(conn)
    assert len(cats["ORG"]) == 2 and len(cats["PAR"]) == 4 and r.issued == {"ORG": 2, "PAR": 4, "ACH": 0, "APP": 0}
    assert {c.username for c in cats["PAR"]} == {"ctf.username", "neo.sample", "trinity_x", "morpheus"}
    assert all(codes.CODE_RE.match(c.verify_code) and c.issue_date == "2026-11-30" and c.status == "valid" for c in store.all_rows(conn))
    assert len({c.suffix for c in store.all_rows(conn)}) == 6


def test_running_again_keeps_every_code_and_issue_date(conn, tmp_path):
    p = issue.read_prelims(write(tmp_path, "p.csv", PRELIMS))
    issue.issue(conn, issue_date="2026-11-30", org=["Jane Doe"], prelims=p)
    before = {(c.category, c.source_ref): (c.verify_code, c.issue_date) for c in store.all_rows(conn)}
    r = issue.issue(conn, issue_date="2026-12-05", org=["Jane Doe"], prelims=p)
    after = {(c.category, c.source_ref): (c.verify_code, c.issue_date) for c in store.all_rows(conn)}
    assert before == after
    assert sum(r.issued.values()) == 0 and r.kept["PAR"] == 4 and r.kept["ORG"] == 1


def test_a_new_person_in_a_later_run_gets_the_later_date(conn, tmp_path):
    issue.issue(conn, issue_date="2026-11-30", org=["Jane Doe"])
    issue.issue(conn, issue_date="2026-12-01", org=["Jane Doe", "Alex Example"])
    dates = {c.name: c.issue_date for c in store.all_rows(conn)}
    assert dates == {"Jane Doe": "2026-11-30", "Alex Example": "2026-12-01"}


def test_a_qualifier_who_reaches_the_finals_ends_with_only_the_finals_code(conn, tmp_path):
    issue.issue(conn, issue_date="2026-11-30", prelims=issue.read_prelims(write(tmp_path, "p.csv", PRELIMS)))
    finals = issue.read_finals(write(tmp_path, "f.csv", "username,studio,rank,user_id\nctf.username,Test,1,11\nNEO.sample,Test,1,\nmorpheus,Null Pointers,16,14\n"))
    r = issue.issue(conn, issue_date="2026-12-12", finals=finals)
    assert r.issued["ACH"] == 2 and r.issued["APP"] == 1 and r.replaced == 3
    rows = store.all_rows(conn)
    valid = {(c.username.lower(), c.category) for c in rows if c.status == "valid"}
    assert valid == {("ctf.username", "ACH"), ("neo.sample", "ACH"), ("morpheus", "APP"), ("trinity_x", "PAR")}
    for old in (c for c in rows if c.status == "replaced"):
        new = store.get(conn, old.replaced_by)
        assert new.status == "valid" and new.category in ("ACH", "APP") and new.username.lower() == old.username.lower()
    app = next(c for c in rows if c.category == "APP")
    assert app.studio_rank == 16 and app.issue_date == "2026-12-12"


def test_a_qualifier_list_read_after_the_finals_gives_finalists_nothing_new(conn, tmp_path):
    issue.issue(conn, issue_date="2026-12-12", finals=issue.read_finals(write(tmp_path, "f.csv", "username,studio,rank,user_id\nctf.username,Test,2,11\n")))
    r = issue.issue(conn, issue_date="2026-12-13", prelims=issue.read_prelims(write(tmp_path, "p.csv", PRELIMS)))
    assert r.skipped_finalists == 1 and r.issued["PAR"] == 3
    assert not [c for c in store.all_rows(conn) if c.category == "PAR" and c.username == "ctf.username"]


def test_ranks_one_to_three_are_winners_and_four_up_are_finalists(conn, tmp_path):
    f = issue.read_finals(write(tmp_path, "f.csv", "username,studio,rank\na,S1,1\nb,S2,2\nc,S3,3\nd,S4,4\ne,S5,40\n"))
    issue.issue(conn, issue_date="2026-12-12", finals=f)
    assert {c.username: c.category for c in store.all_rows(conn)} == {"a": "ACH", "b": "ACH", "c": "ACH", "d": "APP", "e": "APP"}


def test_a_changed_finals_rank_is_reported_not_silently_changed(conn, tmp_path):
    issue.issue(conn, issue_date="2026-12-12", finals=issue.read_finals(write(tmp_path, "f.csv", "username,studio,rank\na,S1,5\n")))
    r = issue.issue(conn, issue_date="2026-12-13", finals=issue.read_finals(write(tmp_path, "f2.csv", "username,studio,rank\na,S1,2\n")))
    assert any("not changed" in w for w in r.warnings)
    assert store.all_rows(conn)[0].studio_rank == 5


def test_people_missing_from_a_later_list_are_reported_never_deleted(conn, tmp_path):
    issue.issue(conn, issue_date="2026-11-30", org=["Jane Doe", "Alex Example"])
    r = issue.issue(conn, issue_date="2026-11-30", org=["Jane Doe"])
    assert len(store.all_rows(conn)) == 2 and any("not in this list" in w for w in r.warnings)


def test_a_refused_record_leaves_the_database_exactly_as_it_was(conn, tmp_path, monkeypatch):
    issue.issue(conn, issue_date="2026-11-30", org=["Jane Doe"])
    before = [(c.verify_code, c.status) for c in store.all_rows(conn)]
    real = store.insert
    calls = {"n": 0}

    def flaky(c, cr):
        calls["n"] += 1
        if calls["n"] == 3:
            raise store.StoreError("simulated refusal")
        return real(c, cr)

    monkeypatch.setattr(store, "insert", flaky)
    with pytest.raises(store.StoreError):
        issue.issue(conn, issue_date="2026-11-30", org=["Jane Doe", "A One", "B Two", "C Three"])
    assert [(c.verify_code, c.status) for c in store.all_rows(conn)] == before


@pytest.mark.parametrize("bad", ["30-11-2026", "2026-13-01", "tomorrow", ""])
def test_the_issue_date_must_be_a_real_date(conn, bad):
    with pytest.raises(issue.IssueError):
        issue.issue(conn, issue_date=bad, org=["Jane Doe"])


def test_the_command_line_end_to_end(tmp_path, capsys):
    from l3mon import cli
    db = tmp_path / "c.sqlite"
    org = write(tmp_path, "org.txt", "Jane Doe\n")
    assert cli.main(["verify", "init", str(db)]) == 0
    assert cli.main(["verify", "init", str(db)]) == 1, "an existing database is never overwritten"
    assert cli.main(["verify", "issue", str(db), "--date", "2026-11-30", "--org", str(org)]) == 0
    code = store.all_rows(store.connect(db))[0].verify_code
    assert cli.main(["verify", "withdraw", str(db), code.lower().replace("-", " "), "--reason", "test"]) == 0
    assert cli.main(["verify", "list", str(db)]) == 0
    out = capsys.readouterr().out
    assert f"{code}  withdrawn" in out and "Name: Jane Doe" in out
    assert cli.main(["verify", "issue", str(db), "--date", "2026-11-30"]) == 1


# ------------------------------------------------------------------ review findings of 26 Nov 2026: who is who

def valid(conn):
    return sorted((c.category, (c.username or c.name).lower()) for c in store.all_rows(conn) if c.status == "valid")


@pytest.mark.parametrize("order", ["prelims-first", "finals-first"])
def test_a_finalist_whose_finals_id_differs_is_still_one_person_by_username(conn, tmp_path, order):
    p = issue.read_prelims(write(tmp_path, "p.csv", "user_id,username,studio\n5,alice,S\n"))
    f = issue.read_finals(write(tmp_path, "f.csv", "username,studio,rank,user_id\nAlice,S,5,77\n"))
    steps = [("p", p), ("f", f)] if order == "prelims-first" else [("f", f), ("p", p)]
    for kind, rows in steps:
        issue.issue(conn, issue_date="2026-12-12", **({"prelims": rows} if kind == "p" else {"finals": rows}))
    assert valid(conn) == [("APP", "alice")], "one valid code whatever the order"


def test_a_row_whose_id_and_username_point_at_two_people_stops_the_run(conn, tmp_path):
    issue.issue(conn, issue_date="2026-11-30", prelims=issue.read_prelims(write(tmp_path, "p.csv", "user_id,username,studio\n5,alice,S\n6,bob,S\n")))
    before = [(c.verify_code, c.status) for c in store.all_rows(conn)]
    with pytest.raises(issue.IssueError, match="different people"):
        issue.issue(conn, issue_date="2026-12-12", finals=issue.read_finals(write(tmp_path, "f.csv", "username,studio,rank,user_id\nbob,S,4,5\n")))
    assert [(c.verify_code, c.status) for c in store.all_rows(conn)] == before


def test_adding_ids_to_a_finals_list_later_issues_nothing_new(conn, tmp_path):
    issue.issue(conn, issue_date="2026-12-12", finals=issue.read_finals(write(tmp_path, "f.csv", "username,studio,rank\nbob,S,2\n")))
    r = issue.issue(conn, issue_date="2026-12-13", finals=issue.read_finals(write(tmp_path, "f2.csv", "username,studio,rank,user_id\nbob,S,2,9\n")))
    assert sum(r.issued.values()) == 0 and valid(conn) == [("ACH", "bob")]


def test_one_person_twice_in_a_finals_file_with_and_without_an_id_is_refused(tmp_path):
    with pytest.raises(issue.IssueError, match="twice"):
        issue.read_finals(write(tmp_path, "f.csv", "username,studio,rank,user_id\nbob,S,4,9\nBob,S,4,\n"))


def test_two_studios_cannot_share_a_rank(tmp_path):
    with pytest.raises(issue.IssueError, match="two studios"):
        issue.read_finals(write(tmp_path, "f.csv", "username,studio,rank\na,S1,1\nb,S2,1\n"))


def test_names_match_across_unicode_forms_and_spacing_but_are_stored_as_given(conn, tmp_path):
    issue.issue(conn, issue_date="2026-11-30", prelims=issue.read_prelims(write(tmp_path, "p.csv", "user_id,username,studio\n5,José,Two  Spaces\n")))
    issue.issue(conn, issue_date="2026-12-12", finals=issue.read_finals(write(tmp_path, "f.csv", "username,studio,rank\nJOSÉ,Two  Spaces,4\n")))
    assert [c.category for c in store.all_rows(conn) if c.status == "valid"] == ["APP"]
    par = next(c for c in store.all_rows(conn) if c.category == "PAR")
    assert par.studio_name == "Two  Spaces", "a double space inside a name is part of the name"


def test_a_withdrawn_code_is_not_replaced_by_a_routine_rerun(conn, tmp_path):
    p = issue.read_prelims(write(tmp_path, "p.csv", PRELIMS))
    issue.issue(conn, issue_date="2026-11-30", prelims=p)
    cheat = next(c for c in store.all_rows(conn) if c.username == "trinity_x")
    store.withdraw(conn, cheat.verify_code, "flag sharing")
    r = issue.issue(conn, issue_date="2026-12-01", prelims=p)
    assert r.held_withdrawn == 1 and sum(r.issued.values()) == 0
    assert not [c for c in store.all_rows(conn) if c.username == "trinity_x" and c.status == "valid"]


def test_withdraw_then_reissue_corrects_a_finals_rank(conn, tmp_path):
    issue.issue(conn, issue_date="2026-12-12", finals=issue.read_finals(write(tmp_path, "f.csv", "username,studio,rank,user_id\nbob,S,2,9\n")))
    old = store.all_rows(conn)[0]
    store.withdraw(conn, old.verify_code, "rank was wrong")
    r = issue.issue(conn, issue_date="2026-12-13", finals=issue.read_finals(write(tmp_path, "f2.csv", "username,studio,rank,user_id\nbob,S,5,9\n")),
                    reissue={old.verify_code})
    assert r.issued["APP"] == 1
    new = next(c for c in store.all_rows(conn) if c.status == "valid")
    assert new.category == "APP" and new.studio_rank == 5 and new.verify_code != old.verify_code


@pytest.mark.parametrize("which", ["valid", "missing", "not-in-lists"])
def test_reissue_is_only_for_a_withdrawn_code_whose_holder_is_in_the_run(conn, tmp_path, which):
    issue.issue(conn, issue_date="2026-11-30", org=["Jane Doe", "Alex Example"])
    jane = next(c for c in store.all_rows(conn) if c.name == "Jane Doe")
    code = {"valid": jane.verify_code, "missing": "L3M0N26-ORG-ZZZZZ", "not-in-lists": jane.verify_code}[which]
    if which == "not-in-lists":
        store.withdraw(conn, code, "test")
    with pytest.raises(issue.IssueError):
        issue.issue(conn, issue_date="2026-11-30", org=["Alex Example"], reissue={code})


def test_the_command_line_reissue_option(tmp_path, capsys):
    from l3mon import cli
    db = tmp_path / "c.sqlite"
    org = write(tmp_path, "org.txt", "Jane Doe\n")
    cli.main(["verify", "init", str(db)])
    cli.main(["verify", "issue", str(db), "--date", "2026-11-30", "--org", str(org)])
    code = store.all_rows(store.connect(db))[0].verify_code
    cli.main(["verify", "withdraw", str(db), code, "--reason", "typo in the name"])
    assert cli.main(["verify", "issue", str(db), "--date", "2026-12-01", "--org", str(org), "--reissue", code.lower()]) == 0
    assert len([c for c in store.all_rows(store.connect(db)) if c.status == "valid"]) == 1


# ------------------------------------------------------------------ second review of 26 Nov 2026

def test_a_qualifier_code_withdrawn_for_cheating_blocks_a_finals_code_too(conn, tmp_path):
    issue.issue(conn, issue_date="2026-11-30", prelims=issue.read_prelims(write(tmp_path, "p.csv", "user_id,username,studio\n7,wobble,S\n")))
    par = store.all_rows(conn)[0]
    store.withdraw(conn, par.verify_code, "flag sharing")
    f = issue.read_finals(write(tmp_path, "f.csv", "username,studio,rank,user_id\nwobble,S,1,7\n"))
    r = issue.issue(conn, issue_date="2026-12-12", finals=f)
    assert r.held_withdrawn == 1 and sum(r.issued.values()) == 0
    r = issue.issue(conn, issue_date="2026-12-12", finals=f, reissue={par.verify_code})
    assert r.issued["ACH"] == 1, "with --reissue the organisers can still decide otherwise"


def test_a_finals_code_keeps_the_id_of_the_qualifier_code_it_replaced(conn, tmp_path):
    issue.issue(conn, issue_date="2026-11-30", prelims=issue.read_prelims(write(tmp_path, "p.csv", "user_id,username,studio\n42,snorkel,S\n")))
    issue.issue(conn, issue_date="2026-12-12", finals=issue.read_finals(write(tmp_path, "f.csv", "username,studio,rank\nsnorkel,S,1\n")))
    r = issue.issue(conn, issue_date="2026-12-13", finals=issue.read_finals(write(tmp_path, "f2.csv", "username,studio,rank,user_id\nsnorkel_v2,S,1,42\n")))
    assert sum(r.issued.values()) == 0
    assert len([c for c in store.all_rows(conn) if c.status == "valid"]) == 1


def test_a_rank_already_held_by_another_studio_is_refused_across_runs(conn, tmp_path):
    issue.issue(conn, issue_date="2026-12-12", finals=issue.read_finals(write(tmp_path, "f.csv", "username,studio,rank\na1,Alpha,1\n")))
    with pytest.raises(issue.IssueError, match="already"):
        issue.issue(conn, issue_date="2026-12-12", finals=issue.read_finals(write(tmp_path, "f2.csv", "username,studio,rank\nb1,Beta,1\n")))


@pytest.mark.parametrize("blank", ["​", " ", "　", "​‌", "﻿"])
def test_a_name_with_no_visible_character_is_refused(tmp_path, blank):
    if blank.strip():
        # an invisible character that is not a space (zero-width, byte-order mark): a typo the reader cannot see, refused
        with pytest.raises(issue.IssueError):
            issue.read_org(write(tmp_path, "org.txt", f"Real Person\n{blank}\n"))
    else:
        # a line of only (non-breaking or ideographic) spaces is a blank line, skipped like any other
        assert issue.read_org(write(tmp_path, "org.txt", f"Real Person\n{blank}\n")) == ["Real Person"]
    with pytest.raises(issue.IssueError):
        issue.read_prelims(write(tmp_path, "p.csv", f"user_id,username,studio\n1,{blank},S\n"))
    with pytest.raises(issue.IssueError):
        issue.read_finals(write(tmp_path, "f.csv", f"username,studio,rank\nx,{blank},4\n"))


def test_init_makes_the_private_folder_and_a_wrong_file_gets_a_clear_message(tmp_path, capsys):
    from l3mon import cli
    db = tmp_path / "not-yet-made" / "deeper" / "c.sqlite"
    assert cli.main(["verify", "init", str(db)]) == 0 and db.is_file()
    text_file = tmp_path / "org.txt"
    text_file.write_text("Jane Doe\n", encoding="utf-8")
    empty = tmp_path / "empty.sqlite"
    empty.write_bytes(b"")
    for wrong in (text_file, empty):
        before = wrong.read_bytes()
        assert cli.main(["verify", "list", str(wrong)]) == 1
        assert "is not a credentials database" in capsys.readouterr().out
        assert wrong.read_bytes() == before, "a wrong file is never changed"


def test_list_prints_any_name_through_a_pipe_on_any_platform(tmp_path):
    import os
    import subprocess
    import sys as _sys
    from pathlib import Path as _P
    db = tmp_path / "c.sqlite"
    org = tmp_path / "org.txt"
    org.write_text("Ŝtelo Ĉirkaŭ\n名無し 太郎\n", encoding="utf-8")
    tools = _P(__file__).resolve().parents[1]
    env = {k: v for k, v in os.environ.items() if k not in ("PYTHONIOENCODING", "PYTHONUTF8")}
    env["PYTHONIOENCODING"] = "cp1252"  # what a Windows pipe gives by default
    run = lambda *a: subprocess.run([_sys.executable, "-m", "l3mon", "verify", *a], cwd=tools, env=env, capture_output=True, timeout=60)  # noqa: E731
    assert run("init", str(db)).returncode == 0
    assert run("issue", str(db), "--date", "2026-11-30", "--org", str(org)).returncode == 0
    res = run("list", str(db))
    assert res.returncode == 0, res.stderr.decode("utf-8", "replace")
    out = res.stdout.decode("utf-8")
    assert "Ŝtelo Ĉirkaŭ" in out and "名無し 太郎" in out


# ------------------------------------------------------------------ permanent organiser codes (27 Nov 2026)
# The organisers' codes were handed out before the tool existed: any five capitals or digits, kept exactly. Every name and code here is made up.

def test_an_organiser_line_may_carry_its_own_code_which_is_kept_exactly(conn, tmp_path):
    org = issue.read_org(write(tmp_path, "org.txt", "Jane Doe\tL3M0N26-ORG-A1O0E\nAlex  Example   l3m0n26-org-77z9q\nNo Code Person\n"))
    assert org == [("Jane Doe", "L3M0N26-ORG-A1O0E"), ("Alex Example", "L3M0N26-ORG-77Z9Q"), "No Code Person"]
    r = issue.issue(conn, issue_date="2026-11-30", org=org)
    held = {c.name: c.verify_code for c in store.all_rows(conn)}
    assert held["Jane Doe"] == "L3M0N26-ORG-A1O0E" and held["Alex Example"] == "L3M0N26-ORG-77Z9Q"
    assert set(held["No Code Person"].rsplit("-", 1)[-1]) <= set(codes.ALPHABET), "a line without a code gets a drawn one"
    assert r.issued["ORG"] == 3
    again = issue.issue(conn, issue_date="2026-12-30", org=org)
    assert again.kept["ORG"] == 3 and again.issued["ORG"] == 0
    assert {c.name: (c.verify_code, c.issue_date) for c in store.all_rows(conn)} == {n: (c, "2026-11-30") for n, c in held.items()}


def test_a_permanent_code_is_never_changed_by_a_later_list(conn, tmp_path):
    issue.issue(conn, issue_date="2026-11-30", org=[("Jane Doe", "L3M0N26-ORG-A1O0E")])
    with pytest.raises(issue.IssueError, match="already holds L3M0N26-ORG-A1O0E"):
        issue.issue(conn, issue_date="2026-11-30", org=[("Jane Doe", "L3M0N26-ORG-B2B2B")])
    assert [c.verify_code for c in store.all_rows(conn)] == ["L3M0N26-ORG-A1O0E"]


def test_a_code_held_by_someone_else_or_listed_twice_stops_the_run(conn, tmp_path):
    issue.issue(conn, issue_date="2026-11-30", org=[("Jane Doe", "L3M0N26-ORG-A1O0E")])
    with pytest.raises(issue.IssueError, match="already held by another record"):
        issue.issue(conn, issue_date="2026-11-30", org=[("Jane Doe", "L3M0N26-ORG-A1O0E"), ("Alex Example", "L3M0N26-ORG-A1O0E")])
    with pytest.raises(issue.IssueError, match="also on line 1"):
        issue.read_org(write(tmp_path, "org.txt", "Jane Doe\tL3M0N26-ORG-A1O0E\nAlex Example\tL3M0N26-ORG-A1O0E\n"))
    assert len(store.all_rows(conn)) == 1


@pytest.mark.parametrize("bad", ["L3M0N26-PAR-BCDFG", "L3M0N26-ORG-A1O0", "L3M0N26-ORG-A1-0E"])
def test_a_line_ending_in_something_that_is_not_an_organiser_code_is_refused(tmp_path, bad):
    with pytest.raises(issue.IssueError):
        issue.read_org(write(tmp_path, "org.txt", f"Jane Doe\t{bad}\n"))


def test_a_drawn_code_never_takes_the_five_characters_of_a_permanent_one(conn, tmp_path):
    # a permanent code made only of consonants could be drawn for a qualifier: the drawing skips it
    issue.issue(conn, issue_date="2026-11-30", org=[("Jane Doe", "L3M0N26-ORG-BBBBB")])
    picks = iter("BBBBB" + "CCCCC" * 10)
    issue.issue(conn, issue_date="2026-11-30", prelims=issue.read_prelims(write(tmp_path, "p.csv", "user_id,username,studio\n11,neo.sample,Test\n")),
                rand=lambda alphabet: next(picks))
    assert {c.verify_code for c in store.all_rows(conn)} == {"L3M0N26-ORG-BBBBB", "L3M0N26-PAR-CCCCC"}


def test_a_permanent_code_whose_characters_a_drawn_code_already_holds_is_refused(conn, tmp_path):
    issue.issue(conn, issue_date="2026-11-30", prelims=issue.read_prelims(write(tmp_path, "p.csv", "user_id,username,studio\n11,neo.sample,Test\n")),
                rand=lambda alphabet: "C")
    with pytest.raises(issue.IssueError, match="already held by another record"):
        issue.issue(conn, issue_date="2026-11-30", org=[("Jane Doe", "L3M0N26-ORG-CCCCC")])


def test_the_database_takes_any_capitals_or_digits_for_an_organiser_and_only_consonants_for_the_rest(conn):
    store.insert(conn, store.Credential(verify_code="L3M0N26-ORG-A1O0E", category="ORG", issue_date="2026-11-30", source_ref="org:x", name="X"))
    for code, cat in (("L3M0N26-PAR-A1O0E", "PAR"), ("L3M0N26-PAR-BCDFA", "PAR"), ("L3M0N26-ORG-a1o0e", "ORG")):
        with pytest.raises(store.StoreError):
            store.insert(conn, store.Credential(verify_code=code, category=cat, issue_date="2026-11-30", source_ref=f"t:{code}", name="Y" if cat == "ORG" else None,
                                                username=None if cat == "ORG" else "y", studio_name=None if cat == "ORG" else "S"))


def test_a_permanent_organiser_code_can_be_typed_loosely_and_still_found():
    assert codes.normalize(" l3m0n26 org a1o0e ") == "L3M0N26-ORG-A1O0E"
    assert codes.normalize("https://verify.l3m0nctf.xyz/L3M0N26-ORG-77777/") == "L3M0N26-ORG-77777"
    assert codes.normalize("L3M0N26-PAR-A1O0E") is None, "only an organiser code may hold vowels or digits"
