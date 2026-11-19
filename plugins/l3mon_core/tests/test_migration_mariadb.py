"""The migrations on a real MariaDB: the six tables, their constraints, running them twice or at the same moment, and rolling them back.

SQLite (the default test database) never runs plugin migrations (CTFd calls create_all there), so this is the only proof that
production gets the schema models.py describes. It needs a MariaDB; tools/run-migration-test.sh starts a throwaway one:

    tools/run-migration-test.sh
"""
import importlib.util
import logging
import os
import threading

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import inspect, text
from sqlalchemy.exc import DBAPIError, OperationalError  # MariaDB reports a failed CHECK as an OperationalError, a key or a foreign key as an IntegrityError

from CTFd.cache import cache
from CTFd.models import Challenges, Configs, Teams, db
from CTFd.plugins.l3mon_core import activity, models
from CTFd.plugins.migrations import current, upgrade
from CTFd.utils import _get_config, get_config, set_config
from tests.helpers import create_ctfd, destroy_ctfd, gen_award, gen_challenge, gen_solve, gen_team, gen_user

URL = os.getenv("TESTING_DATABASE_URL", "")
pytestmark = pytest.mark.skipif(not URL.startswith("mysql"), reason="needs a real MariaDB (tools/run-migration-test.sh)")

FIRST = "7a41c0d2b1e3"
SECOND = "b3d95f0a6c12"
THIRD = "c5e2a7b9d104"
FOURTH = "d8f3a1b4e205"
FIFTH = "e9a4b2c5f306"
REVISION = FIFTH  # the head: the fifth revision adds the activity ledger and the removal records
THIRD_COLUMNS = ("difficulty", "delivery")
FOURTH_COLUMNS = ("story",)
THIRD_CHECKS = {"ck_l3mon_programme_difficulty", "ck_l3mon_programme_delivery"}
VERSION_KEY = "l3mon_core_alembic_version"
MODELS = {m.__tablename__: m for m in (models.Channel, models.Programme, models.Void, models.Bonus, models.Note, models.Audit, models.Activity, models.Removal)}
FIRST_FIVE = {"l3mon_channel", "l3mon_programme", "l3mon_void", "l3mon_bonus", "l3mon_note"}
FIFTH_TABLES = {"l3mon_activity", "l3mon_removal"}


def _load(file_name, module_name):
    here = os.path.dirname(os.path.abspath(models.__file__))
    spec = importlib.util.spec_from_file_location(module_name, os.path.join(here, "migrations", file_name))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def migration_module():
    """The first revision (the five tables)."""
    return _load("7a41c0d2b1e3_create_the_l3mon_tables.py", "l3mon_first_revision")


def audit_module():
    """The second revision (the audit table)."""
    return _load("b3d95f0a6c12_add_the_audit_table.py", "l3mon_second_revision")


def third_module():
    """The third revision (the difficulty and the delivery of a programme)."""
    return _load("c5e2a7b9d104_add_difficulty_and_delivery_to_programmes.py", "l3mon_third_revision")


def fourth_module():
    """The fourth revision (the story column of a programme)."""
    return _load("d8f3a1b4e205_add_story_to_programmes.py", "l3mon_fourth_revision")


def fifth_module():
    """The fifth revision (the activity ledger and the removal records)."""
    return _load("e9a4b2c5f306_add_the_activity_ledger_and_removals.py", "l3mon_fifth_revision")


def programme_columns():
    return {c["name"] for c in inspect(db.engine).get_columns("l3mon_programme")}


def programme_checks():
    return {c["name"] for c in inspect(db.engine).get_check_constraints("l3mon_programme")}


def run_on_connection(fn):
    with db.engine.begin() as conn:
        fn(Operations(MigrationContext.configure(conn)))


def tables():
    return set(inspect(db.engine).get_table_names())


def forget_the_recorded_revision():
    """What an operator does after a manual downgrade: CTFd's runner only goes forward from the revision it has recorded."""
    Configs.query.filter_by(key=VERSION_KEY).delete()
    db.session.commit()
    cache.delete_memoized(_get_config, VERSION_KEY)


def test_loading_the_plugin_creates_the_eight_tables_and_records_the_head_revision():
    app = create_ctfd(enable_plugins=True, user_mode="teams")
    with app.app_context():
        assert set(MODELS) <= tables()
        assert current("l3mon_core") == REVISION
    destroy_ctfd(app)


def test_the_tables_say_what_the_models_say():
    app = create_ctfd(enable_plugins=True, user_mode="teams")
    with app.app_context():
        insp = inspect(db.engine)
        for name, model in MODELS.items():
            real = {c["name"]: c for c in insp.get_columns(name)}
            want = {c.name: c for c in model.__table__.columns}
            assert set(real) == set(want), name
            assert list(real) == list(want), f"{name}: the columns are in the same order"
            for col, spec in want.items():
                assert bool(real[col]["nullable"]) == bool(spec.nullable), f"{name}.{col} nullable"
                assert real[col]["type"].python_type == spec.type.python_type, f"{name}.{col} type"
                length = getattr(spec.type, "length", None)
                if length:
                    assert real[col]["type"].length == length, f"{name}.{col} length"
            real_fks = {(tuple(fk["constrained_columns"]), fk["referred_table"], (fk.get("options") or {}).get("ondelete")) for fk in insp.get_foreign_keys(name)}
            want_fks = {(tuple(fk.parent.name for fk in c.elements), c.elements[0].column.table.name, c.ondelete) for c in model.__table__.foreign_key_constraints}
            assert real_fks == want_fks, f"{name} foreign keys"
            real_unique = {tuple(u["column_names"]) for u in insp.get_unique_constraints(name)}
            want_unique = {tuple(c.name for c in u.columns) for u in model.__table__.constraints if u.__class__.__name__ == "UniqueConstraint"}
            want_unique |= {(c.name,) for c in model.__table__.columns if c.unique}
            assert real_unique == want_unique, f"{name} unique constraints"
            real_checks = {c["name"] for c in insp.get_check_constraints(name)}
            want_checks = {c.name for c in model.__table__.constraints if c.__class__.__name__ == "CheckConstraint"}
            assert real_checks == want_checks, f"{name} check constraints"
    destroy_ctfd(app)


def test_the_database_itself_refuses_what_the_models_refuse():
    app = create_ctfd(enable_plugins=True, user_mode="teams")
    with app.app_context():
        ch = models.Channel(slug="street", name="Street")
        team = gen_team(db, name="t", email="t@e.com", member_count=1)
        db.session.add(ch)
        db.session.commit()
        a, b = gen_challenge(db, name="a"), gen_challenge(db, name="b")
        award = gen_award(db, user_id=team.members[0].id, team_id=team.id, value=5)
        db.session.add(models.Programme(challenge_id=a.id, channel_id=ch.id, cell=0, number=1, slug="alpha"))
        db.session.commit()
        uid = team.members[0].id
        bad_rows = [
            models.Channel(slug="street", name="again"),  # slug
            models.Channel(slug="odd", name="odd", kind="weird"),  # check: kind
            models.Channel(slug="odd2", name="odd", release_state="maybe"),  # check: channel release state
            models.Programme(challenge_id=a.id, channel_id=ch.id, cell=1, number=2, slug="beta"),  # one challenge, one programme
            models.Programme(challenge_id=b.id, channel_id=ch.id, cell=0, number=2, slug="beta"),  # cell
            models.Programme(challenge_id=b.id, channel_id=ch.id, cell=1, number=1, slug="beta"),  # number
            models.Programme(challenge_id=b.id, channel_id=ch.id, cell=1, number=2, slug="alpha"),  # slug
            models.Programme(challenge_id=b.id, channel_id=ch.id, cell=1, number=2, slug="beta", release_state="soon"),  # check: programme release state
            models.Programme(challenge_id=b.id, channel_id=ch.id, cell=1, number=2, slug="beta", difficulty="brutal"),  # check: difficulty
            models.Programme(challenge_id=b.id, channel_id=ch.id, cell=1, number=2, slug="beta", difficulty=""),  # check: difficulty (empty)
            models.Programme(challenge_id=b.id, channel_id=ch.id, cell=1, number=2, slug="beta", delivery="carrier_pigeon"),  # check: delivery
            models.Programme(challenge_id=b.id, channel_id=99999, cell=1, number=2, slug="beta"),  # no such channel
            models.Void(challenge_id=a.id, team_id=team.id, user_id=uid, reason="r", outcome="vanished"),  # check: void outcome
            models.Void(challenge_id=a.id, team_id=team.id, user_id=uid, reason=None),  # a reason is required
            models.Bonus(award_id=award.id, team_id=team.id, user_id=uid, scope="everyone", message="m"),  # check: bonus scope
            models.Bonus(award_id=99999, team_id=team.id, user_id=uid, scope="team", message="m"),  # no such award
            models.Note(team_id=99999, title="t", text="x"),  # no such studio
            models.Audit(actor_id=99999, actor_name="x", action="a", target="t", detail=""),  # no such user
            models.Audit(actor_name=None, action="a", target="t", detail=""),  # a name is required
        ]
        for row in bad_rows:
            db.session.add(row)
            with pytest.raises(DBAPIError):
                db.session.commit()
            db.session.rollback()
    destroy_ctfd(app)


def test_deleting_a_challenge_a_giver_or_a_team_removes_what_hangs_on_it_and_keeps_the_audit_trail():
    app = create_ctfd(enable_plugins=True, user_mode="teams")
    with app.app_context():
        team = gen_team(db, name="t", email="t@e.com", member_count=1)
        uid = team.members[0].id
        boss = gen_user(db, name="boss", email="boss@e.com")
        chal = gen_challenge(db)
        ch = models.Channel(slug="street", name="Street")
        db.session.add(ch)
        db.session.commit()
        db.session.add(models.Programme(challenge_id=chal.id, channel_id=ch.id, cell=0, number=1, slug="alpha"))
        solve = gen_solve(db, user_id=uid, team_id=team.id, challenge_id=chal.id)
        award = gen_award(db, user_id=uid, team_id=team.id, value=25)
        db.session.add(models.Void(challenge_id=chal.id, team_id=team.id, user_id=uid, submission_id=solve.id, voided_by=boss.id, reason="leaked"))
        db.session.add(models.Bonus(award_id=award.id, team_id=team.id, user_id=uid, scope="team", message="bug", given_by=boss.id))
        db.session.add(models.Note(team_id=team.id, title="t", text="x"))
        db.session.add(models.Audit(actor_id=boss.id, actor_name="boss", action="release.set", target="programme:alpha", detail="released"))
        db.session.commit()
        chal_id, team_id, boss_id = chal.id, team.id, boss.id

        # an administrator who gave a bonus and voided a solve leaves: both records stay, with nobody as the author
        db.session.execute(text("DELETE FROM users WHERE id = :i"), {"i": boss_id})
        db.session.commit()
        assert models.Bonus.query.one().given_by is None and models.Void.query.one().voided_by is None
        line = models.Audit.query.one()
        assert line.actor_id is None and line.actor_name == "boss", "the audit line outlives the administrator and still says who"

        # the challenge goes: its programme and its voids go with it
        Challenges.query.filter_by(id=chal_id).delete()
        db.session.commit()
        assert models.Programme.query.count() == 0 and models.Void.query.count() == 0
        assert models.Bonus.query.count() == 1 and models.Note.query.count() == 1, "a bonus and a note are about the studio, not the challenge"

        # the studio goes (CTFd first lets its members go, as its own delete does): its bonuses and its notes go with it
        db.session.execute(text("UPDATE teams SET captain_id = NULL WHERE id = :t"), {"t": team_id})
        db.session.execute(text("UPDATE users SET team_id = NULL WHERE team_id = :t"), {"t": team_id})
        Teams.query.filter_by(id=team_id).delete()
        db.session.commit()
        assert models.Bonus.query.count() == 0 and models.Note.query.count() == 0
        assert models.Channel.query.count() == 1, "a channel does not depend on any of this"
    destroy_ctfd(app)


def test_running_the_migration_again_one_after_another_changes_nothing():
    app = create_ctfd(enable_plugins=True, user_mode="teams")
    with app.app_context():
        before = tables()
        upgrade(plugin_name="l3mon_core", force_all=True)
        upgrade(plugin_name="l3mon_core", force_all=True)
        run_on_connection(lambda op: migration_module().upgrade(op=op))
        run_on_connection(lambda op: audit_module().upgrade(op=op))
        run_on_connection(lambda op: third_module().upgrade(op=op))
        assert tables() == before and current("l3mon_core") == REVISION and set(THIRD_COLUMNS) <= programme_columns()
    destroy_ctfd(app)


@pytest.mark.parametrize("which", ["first", "second"])
def test_two_workers_running_a_migration_at_the_same_moment_both_succeed(which):
    app = create_ctfd(enable_plugins=True, user_mode="teams")
    with app.app_context():
        module = migration_module() if which == "first" else audit_module()
        ours = FIRST_FIVE if which == "first" else {"l3mon_audit"}
        for round_number in range(5):
            run_on_connection(lambda op: module.downgrade(op=op))
            assert not (ours & tables())
            barrier, errors = threading.Barrier(2), []

            def worker():
                try:
                    with app.app_context():  # a worker of its own: its own application context and its own connection
                        with db.engine.begin() as conn:
                            op = Operations(MigrationContext.configure(conn))
                            barrier.wait()
                            module.upgrade(op=op)
                except Exception as e:  # noqa: BLE001
                    errors.append(repr(e))

            threads = [threading.Thread(target=worker) for _ in range(2)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()
            assert not errors, f"round {round_number}: {errors}"
            assert ours <= tables(), f"round {round_number}"
    destroy_ctfd(app)


def test_the_second_downgrade_drops_only_the_audit_table_and_a_database_at_the_first_revision_gets_it_at_the_next_start():
    app = create_ctfd(enable_plugins=True, user_mode="teams")
    with app.app_context():
        before = tables()
        run_on_connection(lambda op: audit_module().downgrade(op=op))
        assert before - tables() == {"l3mon_audit"}, "only the audit table is gone; the five first tables stay"
        run_on_connection(lambda op: audit_module().downgrade(op=op))  # a second downgrade is harmless
        # a platform that was running part 3.1 has the first revision recorded: the next start adds the audit table, once
        set_config("l3mon_core_alembic_version", FIRST)
        assert current("l3mon_core") == FIRST
        upgrade(plugin_name="l3mon_core")
        assert tables() == before and current("l3mon_core") == REVISION
    destroy_ctfd(app)


def test_the_downgrades_drop_the_eight_tables_and_nothing_else_and_a_forgotten_revision_brings_them_back_at_the_next_start():
    app = create_ctfd(enable_plugins=True, user_mode="teams")
    with app.app_context():
        before = tables()
        run_on_connection(lambda op: fifth_module().downgrade(op=op))
        run_on_connection(lambda op: audit_module().downgrade(op=op))
        run_on_connection(lambda op: migration_module().downgrade(op=op))
        after = tables()
        assert before - after == set(MODELS), "exactly our eight tables are gone"
        assert after <= before and "challenges" in after and "teams" in after
        run_on_connection(lambda op: migration_module().downgrade(op=op))  # a second downgrade is harmless
        # CTFd's runner only goes forward from the revision it recorded, so after a manual downgrade the recorded revision must be
        # forgotten too (the documented rollback), and then the next start builds the tables again
        assert current("l3mon_core") == REVISION
        upgrade(plugin_name="l3mon_core")
        assert not (set(MODELS) & tables()), "with the revision still recorded the runner rightly does nothing"
        forget_the_recorded_revision()
        assert current("l3mon_core") is None
        upgrade(plugin_name="l3mon_core")
        assert tables() == before and current("l3mon_core") == REVISION and get_config("user_mode") == "teams"
    destroy_ctfd(app)


def test_the_third_revision_adds_exactly_the_two_columns_keeps_the_rows_and_a_second_run_changes_nothing():
    app = create_ctfd(enable_plugins=True, user_mode="teams")
    with app.app_context():
        assert set(THIRD_COLUMNS) <= programme_columns() and THIRD_CHECKS <= programme_checks()
        ch = models.Channel(slug="street", name="Street")
        db.session.add(ch)
        db.session.commit()
        a = gen_challenge(db, name="a")
        db.session.add(models.Programme(challenge_id=a.id, channel_id=ch.id, cell=0, number=1, slug="alpha", difficulty="hard", delivery="live_single"))
        db.session.commit()
        run_on_connection(lambda op: third_module().downgrade(op=op))
        assert not (set(THIRD_COLUMNS) & programme_columns()) and not (THIRD_CHECKS & programme_checks()), "the downgrade removes the columns and their checks"
        assert db.session.execute(text("SELECT slug FROM l3mon_programme")).scalar() == "alpha", "and keeps the row"
        db.session.rollback()  # a read leaves the session's transaction open, and it would hold the table against the next ALTER
        run_on_connection(lambda op: third_module().downgrade(op=op))  # a second downgrade is harmless
        run_on_connection(lambda op: third_module().upgrade(op=op))
        run_on_connection(lambda op: third_module().upgrade(op=op))  # and so is a second upgrade
        assert set(THIRD_COLUMNS) <= programme_columns() and THIRD_CHECKS <= programme_checks()
        row = db.session.execute(text("SELECT difficulty, delivery FROM l3mon_programme WHERE slug = 'alpha'")).one()
        assert tuple(row) == ("medium", "static_shared"), "a row from before the revision gets the defaults"
    destroy_ctfd(app)


def test_a_run_that_died_between_a_column_and_its_check_is_finished_by_the_next_start():
    """MariaDB commits each ALTER on its own, so a worker killed between the two leaves the column without its CHECK; the revision is
    recorded as done at the next start, so the revision itself has to notice (found by the independent review)."""
    app = create_ctfd(enable_plugins=True, user_mode="teams")
    with app.app_context():
        for check, column in (("ck_l3mon_programme_delivery", "delivery"), ("ck_l3mon_programme_difficulty", "difficulty")):
            with db.engine.begin() as conn:
                conn.execute(text(f"ALTER TABLE l3mon_programme DROP CONSTRAINT {check}"))
            assert check not in programme_checks() and column in programme_columns()
            run_on_connection(lambda op: third_module().upgrade(op=op))
            assert THIRD_CHECKS <= programme_checks() and set(THIRD_COLUMNS) <= programme_columns(), check
    destroy_ctfd(app)


def test_two_workers_adding_the_two_columns_at_the_same_moment_both_succeed():
    app = create_ctfd(enable_plugins=True, user_mode="teams")
    with app.app_context():
        module = third_module()
        for round_number in range(5):
            run_on_connection(lambda op: module.downgrade(op=op))
            assert not (set(THIRD_COLUMNS) & programme_columns())
            barrier, errors = threading.Barrier(2), []

            def worker():
                try:
                    with app.app_context():
                        with db.engine.begin() as conn:
                            op = Operations(MigrationContext.configure(conn))
                            barrier.wait()
                            module.upgrade(op=op)
                except Exception as e:  # noqa: BLE001
                    errors.append(repr(e))

            threads = [threading.Thread(target=worker) for _ in range(2)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()
            assert not errors, f"round {round_number}: {errors}"
            assert set(THIRD_COLUMNS) <= programme_columns() and THIRD_CHECKS <= programme_checks(), f"round {round_number}"
    destroy_ctfd(app)


def test_a_database_at_the_second_revision_gets_the_columns_at_the_next_start():
    app = create_ctfd(enable_plugins=True, user_mode="teams")
    with app.app_context():
        run_on_connection(lambda op: fourth_module().downgrade(op=op))
        run_on_connection(lambda op: third_module().downgrade(op=op))
        set_config("l3mon_core_alembic_version", SECOND)
        assert current("l3mon_core") == SECOND
        upgrade(plugin_name="l3mon_core")
        assert current("l3mon_core") == REVISION and set(THIRD_COLUMNS) <= programme_columns() and THIRD_CHECKS <= programme_checks() and set(FOURTH_COLUMNS) <= programme_columns()
    destroy_ctfd(app)


def test_the_fourth_revision_adds_the_story_column_keeps_the_rows_and_a_second_run_changes_nothing():
    app = create_ctfd(enable_plugins=True, user_mode="teams")
    with app.app_context():
        assert set(FOURTH_COLUMNS) <= programme_columns()
        ch = models.Channel(slug="street", name="Street")
        db.session.add(ch)
        db.session.commit()
        a = gen_challenge(db, name="a")
        db.session.add(models.Programme(challenge_id=a.id, channel_id=ch.id, cell=0, number=1, slug="alpha", story="A cold story."))
        db.session.commit()
        run_on_connection(lambda op: fourth_module().downgrade(op=op))
        assert not (set(FOURTH_COLUMNS) & programme_columns()), "the downgrade removes the story column"
        assert db.session.execute(text("SELECT slug FROM l3mon_programme")).scalar() == "alpha", "and keeps the row"
        db.session.rollback()
        run_on_connection(lambda op: fourth_module().downgrade(op=op))  # a second downgrade is harmless
        run_on_connection(lambda op: fourth_module().upgrade(op=op))
        run_on_connection(lambda op: fourth_module().upgrade(op=op))  # and so is a second upgrade
        assert set(FOURTH_COLUMNS) <= programme_columns()
        row = db.session.execute(text("SELECT story FROM l3mon_programme WHERE slug = 'alpha'")).scalar()
        assert row == "", "a row from before the revision gets the server default empty string"
    destroy_ctfd(app)


def test_two_workers_adding_the_story_column_at_the_same_moment_both_succeed():
    app = create_ctfd(enable_plugins=True, user_mode="teams")
    with app.app_context():
        module = fourth_module()
        for round_number in range(5):
            run_on_connection(lambda op: module.downgrade(op=op))
            assert not (set(FOURTH_COLUMNS) & programme_columns())
            barrier, errors = threading.Barrier(2), []

            def worker():
                try:
                    with app.app_context():
                        with db.engine.begin() as conn:
                            op = Operations(MigrationContext.configure(conn))
                            barrier.wait()
                            module.upgrade(op=op)
                except Exception as e:  # noqa: BLE001
                    errors.append(repr(e))

            threads = [threading.Thread(target=worker) for _ in range(2)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()
            assert not errors, f"round {round_number}: {errors}"
            assert set(FOURTH_COLUMNS) <= programme_columns(), f"round {round_number}"
    destroy_ctfd(app)


# ------------------------------------------------------------------ the fifth revision: the activity ledger and the removal records

ACTIVITY_INDEXES = {"ix_l3mon_activity_at", "ix_l3mon_activity_kind_at", "ix_l3mon_activity_team_id", "ix_l3mon_activity_actor_id"}


def test_the_fifth_revision_adds_the_two_tables_with_their_indexes_and_a_second_run_changes_nothing():
    app = create_ctfd(enable_plugins=True, user_mode="teams")
    with app.app_context():
        assert FIFTH_TABLES <= tables()
        assert ACTIVITY_INDEXES <= {i["name"] for i in inspect(db.engine).get_indexes("l3mon_activity")}
        assert {"ix_l3mon_removal_team_user", "ix_l3mon_removal_team_at"} <= {i["name"] for i in inspect(db.engine).get_indexes("l3mon_removal")}
        before = tables()
        run_on_connection(lambda op: fifth_module().upgrade(op=op))  # again: nothing happens, and no error
        assert tables() == before
        run_on_connection(lambda op: fifth_module().downgrade(op=op))
        assert before - tables() == FIFTH_TABLES, "only the two new tables are gone"
        run_on_connection(lambda op: fifth_module().downgrade(op=op))  # a second downgrade is harmless
        run_on_connection(lambda op: fifth_module().upgrade(op=op))
        assert tables() == before
    destroy_ctfd(app)


def test_the_activity_ledger_keeps_its_rows_when_the_account_or_the_studio_it_names_is_deleted():
    """No foreign keys on the ledger: a deleted studio must not cascade into it (the removal records, which do have them, go with their studio)."""
    app = create_ctfd(enable_plugins=True, user_mode="teams")
    with app.app_context():
        team = gen_team(db, name="Gone Studio", email="g@e.com", member_count=1)
        member = team.members[0]
        row_id = activity.record("studio.leave", actor=member, team_id=team.id, target=f"user:{member.id}")
        db.session.add(models.Removal(team_id=team.id, user_id=member.id, removed_by=None))
        db.session.commit()
        db.session.execute(text("UPDATE users SET team_id = NULL WHERE team_id = :i"), {"i": team.id})
        db.session.execute(text("DELETE FROM teams WHERE id = :i"), {"i": team.id})
        db.session.commit()
        assert models.Removal.query.count() == 0, "a removal record goes with its studio"
        assert models.Activity.query.get(row_id) is not None, "the ledger line stays"
    destroy_ctfd(app)


def test_on_a_database_that_allows_it_the_migration_creates_the_triggers_and_the_database_refuses_changes():
    """This MariaDB has no binary log, so the application user may create triggers (production's has one: see the next test)."""
    app = create_ctfd(enable_plugins=True, user_mode="teams")
    with app.app_context():
        assert activity.triggers_installed()
        row_id = activity.record("k.one", target="t")
        for statement in ("UPDATE l3mon_activity SET kind = 'k.two' WHERE id = :i", "DELETE FROM l3mon_activity WHERE id = :i"):
            with pytest.raises(DBAPIError) as err:
                db.session.execute(text(statement), {"i": row_id})
                db.session.commit()
            db.session.rollback()
            assert "append-only" in str(err.value)
        assert models.Activity.query.get(row_id).kind == "k.one"
        assert activity.verify_chain().ok
    destroy_ctfd(app)


def test_a_database_that_refuses_the_triggers_does_not_stop_the_migration_and_says_what_to_do():
    """Production: MariaDB with binary logging answers ERROR 1419 to CREATE TRIGGER from a user without SUPER. The platform must still start."""
    app = create_ctfd(enable_plugins=True, user_mode="teams")
    with app.app_context():
        run_on_connection(lambda op: fifth_module().downgrade(op=op))
        assert not (FIFTH_TABLES & tables())

        def refuse(op):
            real = op.execute

            def execute(statement, *args, **kwargs):
                if "CREATE TRIGGER" in str(statement):
                    raise OperationalError(str(statement), {}, Exception(1419, "You do not have the SUPER privilege and binary logging is enabled"))
                return real(statement, *args, **kwargs)

            op.execute = execute
            fifth_module().upgrade(op=op)

        warnings = []

        class Grab(logging.Handler):  # pytest's caplog is not wired to this logger in the real-database runner
            def emit(self, record):
                warnings.append(record.getMessage())

        grab = Grab(level=logging.WARNING)
        logging.getLogger("l3mon").addHandler(grab)
        try:
            run_on_connection(refuse)
        finally:
            logging.getLogger("l3mon").removeHandler(grab)
        assert FIFTH_TABLES <= tables(), "the tables are there"
        assert not activity.triggers_installed(), "the triggers are not"
        assert any("activity-triggers.sh" in w for w in warnings), "the warning names the step that creates them"
        assert activity.record("k.one", target="t") is not None, "the ledger works without them (chained, and echoed to the log)"
    destroy_ctfd(app)
