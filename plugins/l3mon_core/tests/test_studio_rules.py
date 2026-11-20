"""Who may do what to a studio: renaming (policy A), removing a member, and rejoining after a removal.

The owner's answers of 19 Nov 2026: "go with your recommendation" for the captain's removal of members, the rename policy and the logging.
  * Rename (policy A): a captain may rename the studio once a day; it is locked for good from the real start (28 Nov 2026, 10:00 IST) or after the studio's first solve,
    whichever comes first; the crew can always rename; every change leaves a ledger line with the old and the new name.
  * Removal: a captain unlinks a member. Their solves, hint unlocks and awards STAY with the studio (the studio earned them), so TRP never moves. The captain and the
    captain themself cannot be removed; at most two removals per studio per day; the removed person cannot join the same studio again until the crew lifts it.

Run through tools/run-ctfd-tests.sh:
    tools/run-ctfd-tests.sh l3mon/ctfd:dev -- -q -p no:randomly -p no:cacheprovider /l3mon_tests/l3mon_core
"""
import datetime
import json

import pytest

from CTFd.models import Solves, Submissions, Teams, Unlocks, Users, db
from CTFd.plugins.l3mon_core import activity, studio_rules
from CTFd.plugins.l3mon_core.models import Activity, Removal
from CTFd.utils import set_config
from tests.helpers import create_ctfd, destroy_ctfd, gen_challenge, gen_hint, gen_solve, gen_team, gen_user

HOUR = 3600
NOW = datetime.datetime(2026, 11, 25, 12, 0, 0)  # three days before the real start


@pytest.fixture()
def world():
    app = create_ctfd(enable_plugins=True, user_mode="teams")
    with app.app_context():
        team = gen_team(db, name="Alpha", email="alpha@example.com", member_count=4)
        members = sorted(team.members, key=lambda u: u.id)
        crew = Users.query.filter_by(name="admin").first()
        yield type("W", (), {"app": app, "team": team, "captain": members[0], "members": members[1:], "crew": crew})
    destroy_ctfd(app)


def rename_in_the_ledger(team, when, by):
    """A rename by `by` that happened `when`, as the ledger would hold it."""
    row_id = activity.record("studio.rename", actor=by, team_id=team.id, target=f"team:{team.id}", detail={"old": "A", "new": "B"})
    from sqlalchemy import text

    # the ledger is append-only, so a test that needs an older line writes it with the triggers out of the way, exactly as a person with full rights could
    db.session.execute(text("DROP TRIGGER IF EXISTS l3mon_activity_no_update"))
    db.session.execute(text("UPDATE l3mon_activity SET at = :w WHERE id = :i"), {"w": when, "i": row_id})
    db.session.commit()


# ------------------------------------------------------------------ the settings and their defaults

def test_the_rename_lock_defaults_to_the_real_start_at_ten_in_the_morning_india_time(world):
    start = datetime.datetime.utcfromtimestamp(studio_rules.rename_locked_from())
    assert start == datetime.datetime(2026, 11, 28, 4, 30, 0), "10:00 IST on 28 November 2026 is 04:30 UTC"


def test_the_crew_can_move_the_rename_lock_and_a_nonsense_value_falls_back_to_the_default(world):
    set_config("l3mon_rename_locked_from", "1800000000")
    assert studio_rules.rename_locked_from() == 1800000000
    for bad in ("tomorrow", "-5", "", "99999999999999"):
        set_config("l3mon_rename_locked_from", bad)
        assert studio_rules.rename_locked_from() == studio_rules.DEFAULT_RENAME_LOCKED_FROM, bad


def test_two_removals_a_day_by_default_and_the_crew_can_change_it(world):
    assert studio_rules.removals_per_day() == 2
    set_config("l3mon_removals_per_day", "3")
    assert studio_rules.removals_per_day() == 3
    set_config("l3mon_removals_per_day", "lots")
    assert studio_rules.removals_per_day() == 2


# ------------------------------------------------------------------ renaming

def test_a_captain_may_rename_before_the_start_when_the_studio_has_not_scored_and_has_not_just_been_renamed(world):
    assert studio_rules.rename_refusal(world.team, "Alpha Two", world.captain, now=NOW) is None


def test_the_crew_may_always_rename_even_after_the_lock_and_after_scoring(world):
    set_config("l3mon_rename_locked_from", "1000000000")
    chal = gen_challenge(db)
    gen_solve(db, user_id=world.captain.id, team_id=world.team.id, challenge_id=chal.id)
    assert studio_rules.rename_refusal(world.team, "Crew Chosen", world.crew, now=NOW) is None


def test_a_second_rename_within_a_day_is_refused_with_the_time_left_and_allowed_after_a_day(world):
    rename_in_the_ledger(world.team, NOW - datetime.timedelta(hours=5), world.captain)
    refusal = studio_rules.rename_refusal(world.team, "Alpha Three", world.captain, now=NOW)
    assert refusal and "once a day" in refusal and "19 hours" in refusal, refusal
    assert studio_rules.rename_refusal(world.team, "Alpha Three", world.captain, now=NOW + datetime.timedelta(hours=20)) is None


def test_a_rename_by_the_crew_or_by_nobody_does_not_use_up_the_captains_day(world):
    """Found by the guard plugin's own test: a studio set up directly in the database (nobody signed in) must still be renameable by its captain, and a crew
    correction of a name must not lock the captain out for a day."""
    rename_in_the_ledger(world.team, NOW - datetime.timedelta(hours=1), world.crew)
    rename_in_the_ledger(world.team, NOW - datetime.timedelta(hours=2), None)
    assert studio_rules.rename_refusal(world.team, "Alpha Two", world.captain, now=NOW) is None


def test_another_studios_rename_does_not_count(world):
    other = gen_team(db, name="Beta", email="beta@example.com", member_count=1)
    rename_in_the_ledger(other, NOW - datetime.timedelta(hours=1), world.captain)
    assert studio_rules.rename_refusal(world.team, "Alpha Two", world.captain, now=NOW) is None


def test_from_the_real_start_the_name_is_locked_for_a_captain(world):
    after = datetime.datetime(2026, 11, 28, 4, 30, 1)
    refusal = studio_rules.rename_refusal(world.team, "Alpha Two", world.captain, now=after)
    assert refusal and "locked" in refusal and "broadcast has started" in refusal, refusal


def test_after_the_studios_first_solve_the_name_is_locked_for_a_captain(world):
    chal = gen_challenge(db)
    gen_solve(db, user_id=world.members[0].id, team_id=world.team.id, challenge_id=chal.id)
    refusal = studio_rules.rename_refusal(world.team, "Alpha Two", world.captain, now=NOW)
    assert refusal and "locked" in refusal and "scored" in refusal, refusal


def test_keeping_the_same_name_is_not_a_rename(world):
    set_config("l3mon_rename_locked_from", "1000000000")
    assert studio_rules.rename_refusal(world.team, world.team.name, world.captain, now=NOW) is None


# ------------------------------------------------------------------ removing a member: who and how often

def test_a_member_in_the_studio_may_be_removed(world):
    assert studio_rules.removal_refusal(world.team, world.members[0], world.captain, now=NOW) is None


def test_the_captain_and_oneself_cannot_be_removed(world):
    refusal = studio_rules.removal_refusal(world.team, world.captain, world.captain, now=NOW)
    assert refusal and "captain" in refusal.lower()
    # the captain's chair handed over: the new captain may not remove themself either
    world.team.captain_id = world.members[0].id
    db.session.commit()
    assert "yourself" in studio_rules.removal_refusal(world.team, world.members[0], world.members[0], now=NOW)


def test_a_person_who_is_not_in_the_studio_cannot_be_removed_from_it(world):
    outsider = gen_user(db, name="outsider", email="outsider@example.com")
    refusal = studio_rules.removal_refusal(world.team, outsider, world.captain, now=NOW)
    assert refusal and "not in your studio" in refusal


def test_at_most_two_removals_a_day_and_the_count_is_per_studio(world):
    other = gen_team(db, name="Beta", email="beta@example.com", member_count=3)
    for member in world.members[:2]:
        studio_rules.remove_member(world.team, member, world.captain, now=NOW)
    db.session.commit()
    refusal = studio_rules.removal_refusal(world.team, world.members[2], world.captain, now=NOW + datetime.timedelta(hours=1))
    assert refusal and "2 members a day" in refusal, refusal
    assert studio_rules.removal_refusal(world.team, world.members[2], world.captain, now=NOW + datetime.timedelta(hours=25)) is None, "a day later it is allowed again"
    beta_member = sorted(other.members, key=lambda u: u.id)[1]
    assert studio_rules.removal_refusal(other, beta_member, sorted(other.members, key=lambda u: u.id)[0], now=NOW) is None, "another studio's count is its own"


# ------------------------------------------------------------------ what removing does, and what it never touches

def test_removing_unlinks_the_member_and_records_who_when_and_where_from_and_keeps_everything_they_earned(world):
    member = world.members[0]
    chal = gen_challenge(db, value=100)
    hint = gen_hint(db, chal.id, content="a clue", cost=10)
    solve = gen_solve(db, user_id=member.id, team_id=world.team.id, challenge_id=chal.id)
    db.session.add(Unlocks(user_id=member.id, team_id=world.team.id, target=hint.id, type="hints", date=NOW - datetime.timedelta(days=3)))
    db.session.commit()
    score_before = Teams.query.get(world.team.id).get_score()
    solves_before, subs_before, unlocks_before = Solves.query.count(), Submissions.query.count(), Unlocks.query.count()

    removal = studio_rules.remove_member(world.team, member, world.captain, now=NOW + datetime.timedelta(days=2))
    db.session.commit()

    assert Users.query.get(member.id).team_id is None
    assert (removal.team_id, removal.user_id, removal.removed_by, removal.lifted_at) == (world.team.id, member.id, world.captain.id, None)
    assert Teams.query.get(world.team.id).get_score() == score_before, "TRP does not move"
    assert (Solves.query.count(), Submissions.query.count(), Unlocks.query.count()) == (solves_before, subs_before, unlocks_before), "solves, submissions and unlocks all stay"
    assert Solves.query.get(solve.id).team_id == world.team.id, "the solve still belongs to the studio"


def test_a_removal_leaves_a_leave_line_and_a_remove_line_in_the_ledger(world):
    member = world.members[0]
    before = Activity.query.count()
    studio_rules.remove_member(world.team, member, world.captain, now=NOW)
    db.session.commit()
    rows = Activity.query.order_by(Activity.id).all()[before:]
    kinds = [r.kind for r in rows]
    assert "studio.leave" in kinds and "studio.remove" in kinds
    removal_line = next(r for r in rows if r.kind == "studio.remove")
    assert removal_line.team_id == world.team.id and removal_line.target == f"user:{member.id}"
    assert json.loads(removal_line.detail)["name"] == member.name
    assert removal_line.actor_id == world.captain.id


def test_a_member_with_a_solve_or_an_unlock_in_the_last_day_is_flagged_for_the_crew(world):
    busy, quiet = world.members[0], world.members[1]
    chal = gen_challenge(db)
    solve = gen_solve(db, user_id=busy.id, team_id=world.team.id, challenge_id=chal.id)
    solve.date = NOW - datetime.timedelta(hours=3)
    db.session.commit()
    flagged = studio_rules.remove_member(world.team, busy, world.captain, now=NOW)
    plain = studio_rules.remove_member(world.team, quiet, world.captain, now=NOW + datetime.timedelta(minutes=1))
    db.session.commit()
    assert flagged.recent_activity == 1 and plain.recent_activity == 0
    assert json.loads(Activity.query.filter_by(kind="studio.remove", target=f"user:{busy.id}").one().detail)["recent_activity"] is True


# ------------------------------------------------------------------ rejoining

def test_a_removed_person_cannot_join_the_same_studio_again_but_can_join_another(world):
    member = world.members[0]
    studio_rules.remove_member(world.team, member, world.captain, now=NOW)
    db.session.commit()
    refusal = studio_rules.joining_refusal(member.id, world.team.id)
    assert refusal and "removed" in refusal and "ask the crew" in refusal.lower()
    other = gen_team(db, name="Beta", email="beta@example.com", member_count=1)
    assert studio_rules.joining_refusal(member.id, other.id) is None
    assert studio_rules.joining_refusal(world.members[1].id, world.team.id) is None, "somebody who was never removed is unaffected"


def test_when_the_crew_lifts_the_removal_the_person_may_join_again_and_the_lifting_is_recorded(world):
    member = world.members[0]
    removal = studio_rules.remove_member(world.team, member, world.captain, now=NOW)
    db.session.commit()
    studio_rules.lift(removal, world.crew)
    db.session.commit()
    assert studio_rules.joining_refusal(member.id, world.team.id) is None
    saved = Removal.query.get(removal.id)
    assert saved.lifted_by == world.crew.id and saved.lifted_at is not None
    assert Activity.query.filter_by(kind="studio.removal.lift").count() == 1
