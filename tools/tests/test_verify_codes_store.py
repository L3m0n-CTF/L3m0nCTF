"""Verification codes and the credentials database (tools/l3mon/verify/codes.py, store.py).

The overview the organisers fixed on 26 Nov 2026 is the contract: four categories, exactly these shown fields per category, one code per person,
`L3M0N26-<CAT>-<5 letters>`. The database must refuse anything else on its own, whatever tool or hand edit tries it.
"""
from __future__ import annotations

import sqlite3

import pytest

from l3mon.verify import codes, store


# ------------------------------------------------------------------ codes

def test_a_code_has_the_agreed_shape_and_alphabet():
    assert codes.PREFIX == "L3M0N26"
    assert codes.CATEGORIES == ("ORG", "PAR", "ACH", "APP")
    assert codes.ALPHABET == "BCDFGHJKLMNPQRSTVWXYZ" and len(codes.ALPHABET) == 21
    assert not set("AEIOU") & set(codes.ALPHABET), "no vowels: no I/O look-alikes and no accidental words"
    assert codes.make("PAR", "BKXQT") == "L3M0N26-PAR-BKXQT"
    for bad in [("PAR", "BKXQ"), ("PAR", "BKXQTT"), ("PAR", "BKXQA"), ("XYZ", "BKXQT"), ("par", "BKXQT"), ("PAR", "bkxqt")]:
        with pytest.raises(ValueError):
            codes.make(*bad)


def test_new_suffixes_are_random_letters_from_the_alphabet_and_never_repeat():
    taken: set[str] = set()
    for _ in range(3000):
        s = codes.new_suffix(taken)
        assert len(s) == 5 and set(s) <= set(codes.ALPHABET)
        assert s not in taken
        taken.add(s)


def test_a_taken_suffix_is_skipped():
    seq = iter("BBBBB" "BBBBB" "CCCCC")
    assert codes.new_suffix({"BBBBB"}, rand=lambda _alphabet: next(seq)) == "CCCCC"


@pytest.mark.parametrize("typed, canon", [
    ("L3M0N26-PAR-BKXQT", "L3M0N26-PAR-BKXQT"),
    ("  l3m0n26-par-bkxqt ", "L3M0N26-PAR-BKXQT"),
    ("L3M0N26PARBKXQT", "L3M0N26-PAR-BKXQT"),
    ("l3m0n26 ach zmpld", "L3M0N26-ACH-ZMPLD"),
    ("L3MON26-APP-QNWSC", "L3M0N26-APP-QNWSC"),          # the letter O typed for the zero
    ("https://verify.l3m0nctf.xyz/L3M0N26-ORG-HRVTQ", "L3M0N26-ORG-HRVTQ"),
    ("verify.l3m0nctf.xyz/l3m0n26-org-hrvtq/", "L3M0N26-ORG-HRVTQ"),
    ("L3M0N26_PAR_BKXQT", "L3M0N26-PAR-BKXQT"),
])
def test_a_typed_code_is_read_back_forgivingly(typed, canon):
    assert codes.normalize(typed) == canon


@pytest.mark.parametrize("typed", [
    "", "hello", "L3M0N26-XYZ-BKXQT", "L3M0N26-PAR-BKXQA", "L3M0N26-PAR-BKXQ", "L3M0N26-PAR-BKXQTT", "L3M0N25-PAR-BKXQT",
    "L3M0N26-PAR-BKX0T", "x" * 500, None, 12345,
])
def test_anything_that_cannot_be_a_code_is_refused(typed):
    assert codes.normalize(typed) is None


@pytest.mark.parametrize("n, text", [(1, "1st"), (2, "2nd"), (3, "3rd"), (4, "4th"), (11, "11th"), (12, "12th"), (13, "13th"), (16, "16th"),
                                     (21, "21st"), (22, "22nd"), (23, "23rd"), (101, "101st"), (111, "111th"), (112, "112th")])
def test_ranks_read_as_ordinals(n, text):
    assert codes.ordinal(n) == text


@pytest.mark.parametrize("bad", [0, -1, True, 1.0, "1"])
def test_a_rank_must_be_a_whole_number_from_one(bad):
    with pytest.raises(ValueError):
        codes.ordinal(bad)


# ------------------------------------------------------------------ the database refuses wrong shapes

@pytest.fixture
def db(tmp_path):
    conn = store.connect(tmp_path / "c.sqlite", create=True)
    yield conn
    conn.close()


def cred(category="PAR", suffix="BKXQT", **kw):
    base = {"ORG": dict(name="Jane Doe"), "PAR": dict(username="ctf.username", studio_name="Test"),
            "ACH": dict(username="ctf.username", studio_name="Test", studio_rank=1),
            "APP": dict(username="ctf.username", studio_name="Test", studio_rank=16)}[category]
    base.update(kw)
    return store.Credential(verify_code=f"L3M0N26-{category}-{suffix}", category=category, issue_date=kw.pop("issue_date", "2026-11-30"),
                            source_ref=kw.pop("source_ref", f"ref:{suffix}"), **{k: v for k, v in base.items() if k not in ("issue_date", "source_ref")})


@pytest.mark.parametrize("category", codes.CATEGORIES)
def test_each_category_stores_exactly_its_agreed_fields(db, category):
    c = store.insert(db, cred(category))
    got = store.get(db, c.verify_code)
    assert got.event_name == store.EVENT[category] and got.role == store.ROLE[category]
    labels = [k for k, _ in got.shown()]
    assert labels == {
        "ORG": ["Event Name", "Role", "Name", "Issue Date"],
        "PAR": ["Event Name", "Role", "Username", "Studio Name", "Issue Date"],
        "ACH": ["Event Name", "Role", "Username", "Studio Name", "Studio Rank", "Issue Date"],
        "APP": ["Event Name", "Role", "Username", "Studio Name", "Studio Rank", "Issue Date"],
    }[category]


def test_the_agreed_event_names_and_roles():
    assert store.EVENT == {"ORG": "L3m0n CTF 2026", "PAR": "L3m0n CTF 2026 Qualifiers", "ACH": "L3m0n CTF 2026 Finals", "APP": "L3m0n CTF 2026 Finals"}
    assert store.ROLE == {"ORG": "Organizer", "PAR": "Participant", "ACH": "Winner", "APP": "Participant"}


@pytest.mark.parametrize("category, change", [
    ("ORG", dict(username="someone")), ("ORG", dict(studio_name="Test")), ("ORG", dict(name="  ")), ("ORG", dict(studio_rank=1)),
    ("PAR", dict(studio_rank=3)), ("PAR", dict(name="Jane")), ("PAR", dict(studio_name="")), ("PAR", dict(username=None)),
    ("ACH", dict(studio_rank=4)), ("ACH", dict(studio_rank=None)), ("ACH", dict(studio_rank=0)),
    ("APP", dict(studio_rank=3)), ("APP", dict(studio_rank=None)),
    ("PAR", dict(role="Winner")), ("ACH", dict(event_name="L3m0n CTF 2026")),
    ("PAR", dict(issue_date="30/11/2026")),
])
def test_a_record_of_the_wrong_shape_is_refused_by_the_database_itself(db, category, change):
    with pytest.raises(store.StoreError):
        store.insert(db, cred(category, **change))


def test_a_code_must_match_its_category_and_the_alphabet(db):
    bad = cred("PAR")
    bad.verify_code = "L3M0N26-ACH-BKXQT"
    with pytest.raises(store.StoreError):
        store.insert(db, bad)
    bad = cred("PAR", suffix="BKXQA")
    with pytest.raises(store.StoreError):
        store.insert(db, bad)


def test_the_five_letters_are_unique_across_categories(db):
    store.insert(db, cred("PAR", "BKXQT", source_ref="a"))
    with pytest.raises(store.StoreError):
        store.insert(db, cred("ORG", "BKXQT", source_ref="b"))


def test_one_code_per_person_and_category(db):
    store.insert(db, cred("PAR", "BKXQT", source_ref="prelims:user:7"))
    with pytest.raises(store.StoreError):
        store.insert(db, cred("PAR", "CDFGH", source_ref="prelims:user:7"))


def test_a_raw_sql_edit_cannot_bypass_the_rules(db):
    store.insert(db, cred("ACH"))
    with pytest.raises(sqlite3.IntegrityError):
        db.execute("UPDATE credentials SET studio_rank = 9 WHERE category = 'ACH'")
    with pytest.raises(sqlite3.IntegrityError):
        db.execute("UPDATE credentials SET status = 'withdrawn' WHERE category = 'ACH'")  # no reason


def test_withdraw_needs_a_reason_and_happens_once(db):
    c = store.insert(db, cred("PAR"))
    with pytest.raises(store.StoreError):
        store.withdraw(db, c.verify_code, "  ")
    w = store.withdraw(db, c.verify_code, "found sharing flags")
    assert w.status == "withdrawn" and w.withdrawn_reason == "found sharing flags"
    with pytest.raises(store.StoreError):
        store.withdraw(db, c.verify_code, "again")
    with pytest.raises(store.StoreError):
        store.withdraw(db, "L3M0N26-PAR-ZZZZZ", "missing")


def test_a_file_that_is_not_a_credentials_database_is_refused(tmp_path):
    with pytest.raises(store.StoreError):
        store.connect(tmp_path / "missing.sqlite")
    other = tmp_path / "other.sqlite"
    sqlite3.connect(str(other)).execute("CREATE TABLE meta (key TEXT, value TEXT)").connection.commit()
    with pytest.raises(store.StoreError):
        store.connect(other)


# ------------------------------------------------------------------ review findings of 26 Nov 2026

@pytest.mark.parametrize("category, change", [
    ("APP", dict(studio_rank="abc")), ("APP", dict(studio_rank="4x")), ("ACH", dict(studio_rank=2.5)),
    ("ORG", dict(name="\t\n")), ("PAR", dict(username="\r\n ")), ("PAR", dict(studio_name="\t")),
    ("PAR", dict(issue_date="2026-19-39")), ("PAR", dict(issue_date="2026-02-30")),
], ids=["rank-text", "rank-mixed", "rank-real", "name-whitespace", "username-whitespace", "studio-tab", "date-impossible", "date-feb30"])
def test_the_database_refuses_odd_values_too(db, category, change):
    with pytest.raises(store.StoreError):
        store.insert(db, cred(category, **change))


def test_a_withdrawn_persons_code_can_be_reissued_but_two_valid_ones_cannot_exist(db):
    first = store.insert(db, cred("PAR", "BKXQT", source_ref="prelims:user:7"))
    store.withdraw(db, first.verify_code, "test")
    store.insert(db, cred("PAR", "CDFGH", source_ref="prelims:user:7"))
    with pytest.raises(store.StoreError):
        store.insert(db, cred("PAR", "DFGHJ", source_ref="prelims:user:7"))
