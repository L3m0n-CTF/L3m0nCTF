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
