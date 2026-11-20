"""The activity ledger: what every participant did, written so that it can be trusted afterwards.

Owner's rule (19 Nov 2026): logging of every participant action is critical. The ledger (l3mon_activity) is append-only (a database trigger refuses UPDATE and DELETE),
chained (every row carries the hash of the row before it, so a changed or missing row is found), written in its own short transaction (a slow request never
holds the chain), echoed as one JSON line to the container output (the log shipper takes it to Loki, a second copy that the database cannot erase), and it can
never make an action fail (a ledger error is printed as a line marked "lost" and the request carries on).

Run through tools/run-ctfd-tests.sh:
    tools/run-ctfd-tests.sh l3mon/ctfd:dev -- -q -p no:randomly -p no:cacheprovider /l3mon_tests/l3mon_core
"""
import datetime
import json

import pytest
from sqlalchemy import text

from CTFd.models import Teams, Users, db
from CTFd.plugins.l3mon_core import activity
from CTFd.plugins.l3mon_core.models import Activity
from tests.helpers import create_ctfd, destroy_ctfd, gen_team


def empty_the_ledger():
    """CTFd's own test set-up signs the first administrator in, which the platform (rightly) records. These tests count lines and look at the chain from its first row,
    so they start from an empty ledger: the delete trigger is put aside for a moment, as somebody with full database rights could."""
    db.session.execute(text("DROP TRIGGER IF EXISTS l3mon_activity_no_delete"))
    db.session.execute(text("DELETE FROM l3mon_activity"))
    db.session.execute(text("CREATE TRIGGER l3mon_activity_no_delete BEFORE DELETE ON l3mon_activity BEGIN SELECT RAISE(ABORT, 'l3mon_activity is append-only'); END"))
    db.session.commit()


@pytest.fixture()
def app():
    app = create_ctfd(enable_plugins=True, user_mode="teams")
    with app.app_context():
        empty_the_ledger()
    yield app
    destroy_ctfd(app)


def lines(capsys):
    return [json.loads(line) for line in capsys.readouterr().out.splitlines() if line.startswith("{") and '"l3mon_activity"' in line]


# ------------------------------------------------------------------ what a line holds

def test_a_line_records_who_what_where_and_when(app):
    with app.app_context():
        admin = Users.query.filter_by(name="admin").first()
        with app.test_request_context("/login", headers={"User-Agent": "Mozilla/5.0 (test)", "X-Request-ID": "req-123"}, environ_base={"REMOTE_ADDR": "203.0.113.7"}):
            row_id = activity.record("auth.login.ok", actor=admin, team_id=None, target=f"user:{admin.id}", detail={"via": "password"})
        row = Activity.query.get(row_id)
        assert (row.kind, row.actor_id, row.actor_name, row.target) == ("auth.login.ok", admin.id, "admin", f"user:{admin.id}")
        assert json.loads(row.detail) == {"via": "password"}
        assert (row.ip, row.user_agent, row.request_id) == ("203.0.113.7", "Mozilla/5.0 (test)", "req-123")
        assert abs((datetime.datetime.utcnow() - row.at).total_seconds()) < 30


def test_without_a_person_the_line_says_system(app):
    with app.app_context():
        row_id = activity.record("system.tick", target="clock")
        row = Activity.query.get(row_id)
        assert (row.actor_id, row.actor_name) == (None, "system")


def test_the_name_is_copied_so_deleting_the_account_later_erases_nothing(app):
    with app.app_context():
        team = gen_team(db, name="Alpha", email="a@example.com", member_count=1)
        member = Users.query.filter_by(team_id=team.id).first()
        row_id = activity.record("studio.leave", actor=member, team_id=team.id, target=f"user:{member.id}")
        name, uid = member.name, member.id
        db.session.delete(member)
        db.session.commit()
        row = Activity.query.get(row_id)
        assert (row.actor_id, row.actor_name) == (uid, name), "no foreign key: the ledger never changes when an account is deleted"


def test_a_long_detail_is_clipped_never_refused(app):
    with app.app_context():
        row_id = activity.record("x.long", target="t" * 500, detail={"text": "y" * 5000})
        row = Activity.query.get(row_id)
        assert row is not None and len(row.target) <= 100 and len(row.detail) <= 1000


# ------------------------------------------------------------------ never a secret, never a flag

def test_passwords_tokens_and_flags_never_reach_the_ledger_or_the_log_line(app, capsys):
    # the flag-shaped text is put together here, so that this public file never holds one (tools/l3mon/hygiene.py would refuse it)
    flag_text = "L3m0n" + "{" + "the_real_flag" + "}"
    with app.app_context():
        row_id = activity.record(
            "x.secret", target="user:1",
            detail={"password": "hunter2", "new_password": "hunter3", "token": "abc123token", "csrf": "nonce999", "name": "visible", "note": f"I sent {flag_text} by mistake"},
        )
        row = Activity.query.get(row_id)
        out = capsys.readouterr().out
        for secret in ("hunter2", "hunter3", "abc123token", "nonce999", "the_real_flag"):
            assert secret not in row.detail and secret not in out, secret
        assert json.loads(row.detail)["name"] == "visible"


# ------------------------------------------------------------------ one JSON line to the container output

def test_every_line_is_also_one_json_line_on_standard_output(app, capsys):
    with app.app_context():
        capsys.readouterr()
        row_id = activity.record("studio.create", team_id=7, target="team:7", detail={"name": "Alpha"})
        found = lines(capsys)
        assert len(found) == 1
        line = found[0]
        assert line["id"] == row_id and line["kind"] == "studio.create" and line["team_id"] == 7 and line["detail"] == {"name": "Alpha"}
        assert line["hash"] == Activity.query.get(row_id).hash


# ------------------------------------------------------------------ the chain

def test_each_row_carries_the_hash_of_the_row_before_and_the_chain_verifies(app):
    with app.app_context():
        ids = [activity.record(f"k.{n}", target=f"t{n}") for n in range(4)]
        rows = [Activity.query.get(i) for i in ids]
        assert rows[0].prev_hash == activity.GENESIS
        for before, after in zip(rows, rows[1:]):
            assert after.prev_hash == before.hash
        result = activity.verify_chain()
        assert result.ok and result.checked == 4 and result.first_bad_id is None


def test_an_empty_ledger_verifies(app):
    with app.app_context():
        assert activity.verify_chain().ok


def test_a_changed_row_is_found(app):
    with app.app_context():
        ids = [activity.record(f"k.{n}", target=f"t{n}") for n in range(3)]
        db.session.execute(text("DROP TRIGGER l3mon_activity_no_update"))  # what somebody with full database rights could do
        db.session.execute(text("UPDATE l3mon_activity SET target = 'forged' WHERE id = :i"), {"i": ids[1]})
        db.session.commit()
        result = activity.verify_chain()
        assert not result.ok and result.first_bad_id == ids[1]


def test_a_missing_row_is_found(app):
    with app.app_context():
        ids = [activity.record(f"k.{n}", target=f"t{n}") for n in range(3)]
        db.session.execute(text("DROP TRIGGER l3mon_activity_no_delete"))
        db.session.execute(text("DELETE FROM l3mon_activity WHERE id = :i"), {"i": ids[1]})
        db.session.commit()
        result = activity.verify_chain()
        assert not result.ok and result.first_bad_id == ids[2], "the row after the gap no longer follows its predecessor"


# ------------------------------------------------------------------ append only

def test_the_database_refuses_to_change_a_row(app):
    with app.app_context():
        row_id = activity.record("k.one", target="t")
        with pytest.raises(Exception) as err:
            db.session.execute(text("UPDATE l3mon_activity SET kind = 'k.two' WHERE id = :i"), {"i": row_id})
            db.session.commit()
        db.session.rollback()
        assert "append-only" in str(err.value)
        assert Activity.query.get(row_id).kind == "k.one"


def test_the_database_refuses_to_delete_a_row(app):
    with app.app_context():
        row_id = activity.record("k.one", target="t")
        with pytest.raises(Exception) as err:
            db.session.execute(text("DELETE FROM l3mon_activity WHERE id = :i"), {"i": row_id})
            db.session.commit()
        db.session.rollback()
        assert "append-only" in str(err.value)
        assert Activity.query.count() == 1


# ------------------------------------------------------------------ it can never make an action fail

def test_a_ledger_failure_is_printed_as_a_lost_line_and_the_caller_carries_on(app, monkeypatch, capsys):
    with app.app_context():
        monkeypatch.setattr(activity, "_write", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("database gone")))
        capsys.readouterr()
        result = activity.record("auth.login.ok", target="user:1")
        assert result is None
        found = lines(capsys)
        assert found and found[-1].get("lost") is True and found[-1]["kind"] == "auth.login.ok"


# ------------------------------------------------------------------ deferred lines follow the transaction they belong to

def test_a_queued_line_is_written_when_the_transaction_commits(app):
    with app.app_context():
        activity.queue("studio.rename", team_id=1, target="team:1", detail={"old": "A", "new": "B"})
        assert Activity.query.count() == 0, "nothing yet: the action is not committed"
        db.session.commit()
        assert [r.kind for r in Activity.query.all()] == ["studio.rename"]


def test_a_queued_line_is_dropped_when_the_transaction_rolls_back(app):
    with app.app_context():
        db.session.add(Users(name="somebody", email="somebody@example.com", password="password"))
        db.session.flush()  # a real action: a database transaction is open, and the line is queued inside it
        activity.queue("studio.rename", team_id=1, target="team:1")
        db.session.rollback()
        db.session.commit()
        assert Users.query.filter_by(name="somebody").count() == 0
        assert Activity.query.count() == 0, "a rolled-back action never happened, and the ledger must not say it did"


# ------------------------------------------------------------------ changes to studios are seen wherever they come from

def test_renaming_a_studio_by_any_path_leaves_a_line_with_the_old_and_the_new_name(app):
    with app.app_context():
        team = gen_team(db, name="Alpha", email="a@example.com", member_count=1)
        admin = Users.query.filter_by(name="admin").first()
        with app.test_request_context("/x"):
            team.name = "Alpha Two"
            db.session.commit()
        rows = Activity.query.filter_by(kind="studio.rename").all()
        assert len(rows) == 1
        assert rows[0].team_id == team.id and json.loads(rows[0].detail) == {"old": "Alpha", "new": "Alpha Two"}


def test_joining_leaving_and_a_new_captain_are_seen_wherever_they_come_from(app):
    with app.app_context():
        team = gen_team(db, name="Alpha", email="a@example.com", member_count=2)
        loner = Users(name="loner", email="loner@example.com", password="password")
        db.session.add(loner)
        db.session.commit()
        before = Activity.query.count()
        loner.team_id = team.id
        db.session.commit()
        team.captain_id = loner.id
        db.session.commit()
        loner.team_id = None
        db.session.commit()
        kinds = [r.kind for r in Activity.query.order_by(Activity.id).all()][before:]
        assert kinds == ["studio.join", "studio.captain", "studio.leave"]


def test_a_line_queued_during_a_request_names_the_signed_in_person_and_not_system(app):
    """Found on 20 Nov 2026: the name used to be looked up after the commit, where SQLAlchemy forbids queries, the failure was swallowed, and every rename by a
    signed-in captain was attributed to "system"."""
    from flask import session

    with app.app_context():
        team = gen_team(db, name="Alpha", email="a@example.com", member_count=2)
        captain = Users.query.get(team.captain_id)
        captain_id, captain_name, team_id = captain.id, captain.name, team.id
        with app.test_request_context("/team", environ_base={"REMOTE_ADDR": "198.51.100.9"}):
            session["id"] = captain_id
            Teams.query.get(team_id).name = "Alpha Two"
            db.session.commit()
        line = Activity.query.filter_by(kind="studio.rename").one()
        assert (line.actor_id, line.actor_name) == (captain_id, captain_name)
        assert line.ip == "198.51.100.9"


# ------------------------------------------------------------------ a rename's line lives and dies with the rename itself

def test_a_rename_line_is_written_inside_the_renames_own_transaction_so_a_rollback_takes_it_with_it(app, capsys):
    """The once-a-day rule reads the ledger. If the line were written after the commit, a second request that was waiting for the studio's row lock could read the
    ledger in the instant between the two and be let through (a review finding of 20 Nov 2026). Written in the same transaction, the line exists exactly when the
    rename does."""
    with app.app_context():
        team = gen_team(db, name="Alpha", email="a@example.com", member_count=1)
        capsys.readouterr()
        Teams.query.get(team.id).name = "Alpha Two"
        db.session.flush()
        assert Activity.query.filter_by(kind="studio.rename").count() == 1, "the line is already there, inside the transaction that holds the studio's lock and before it is released"
        db.session.rollback()
        assert Activity.query.filter_by(kind="studio.rename").count() == 0, "rolled back with the rename"
        assert not [l for l in lines(capsys) if l["kind"] == "studio.rename"], "and never printed"


def test_the_log_line_of_a_rename_is_printed_only_after_the_commit_that_made_it_real(app, capsys):
    with app.app_context():
        team = gen_team(db, name="Alpha", email="a@example.com", member_count=1)
        capsys.readouterr()
        Teams.query.get(team.id).name = "Alpha Two"
        db.session.flush()
        assert not [l for l in lines(capsys) if l["kind"] == "studio.rename"], "not yet: the rename is not committed"
        db.session.commit()
        printed = [l for l in lines(capsys) if l["kind"] == "studio.rename"]
        assert len(printed) == 1 and printed[0]["detail"] == {"old": "Alpha", "new": "Alpha Two"} and printed[0]["hash"] == Activity.query.filter_by(kind="studio.rename").one().hash
