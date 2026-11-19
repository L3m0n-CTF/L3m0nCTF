"""The captain's controls over the studio, through real requests: removing a member, renaming, and a removed person trying to come back.

Rules are in plugins/l3mon_core/studio_rules.py (tested there); this file proves they are wired to every door: the removal route, the rename FORM (POST /team), the
rename API (PATCH /api/v1/teams/me), and the two ways into a studio (its name and password, an invite link).

Run through tools/run-ctfd-tests.sh:
    tools/run-ctfd-tests.sh l3mon/ctfd:dev -- -q -p no:randomly -p no:cacheprovider /l3mon_tests/l3mon_theme
"""
import json

import pytest

from CTFd.models import Solves, Teams, Users, db
from CTFd.plugins.l3mon_core.models import Activity, Removal
from CTFd.utils import set_config
from tests.helpers import create_ctfd, destroy_ctfd, gen_challenge, gen_solve, gen_team, login_as_user

# the session keys the studio page reads (plugins/l3mon_theme/pages/team_settings.py); the module cannot be imported before an app exists
FLASH_NOTICE_KEY = "_l3mon_acct_notice"
FLASH_ERROR_KEY = "_l3mon_acct_errors"


def nonce_of(client):
    with client.session_transaction() as sess:
        return sess.get("nonce")


def flash(client, key):
    with client.session_transaction() as sess:
        return sess.get(key)


class World:
    pass


class Plain:
    """A plain copy of a row (id and name): login_as_user opens an app context of its own, and live rows would be detached when it closes."""

    def __init__(self, row):
        self.id, self.name = row.id, row.name


def invite_code(world):
    return Teams.query.get(world.team.id).get_invite_code()


@pytest.fixture()
def world():
    app = create_ctfd(enable_plugins=True, ctf_theme="l3mon", user_mode="teams")  # the l3mon theme, as in production: its 403 page carries the refusal wording
    with app.app_context():
        w = World()
        w.app = app
        team = gen_team(db, name="Alpha", email="alpha@example.com", member_count=4)
        users = sorted(team.members, key=lambda u: u.id)
        w.team, w.captain, w.members = Plain(team), Plain(users[0]), [Plain(u) for u in users[1:]]
        w.captain_client = login_as_user(app, w.captain.name)
        w.member_client = login_as_user(app, w.members[0].name)
        yield w
    destroy_ctfd(app)


def remove(client, member_id, nonce=True):
    data = {"nonce": nonce_of(client)} if nonce else {}
    return client.post(f"/team/members/{member_id}/remove", data=data)


# ------------------------------------------------------------------ removing a member

def test_a_captain_removes_a_member_and_what_the_member_earned_stays_with_the_studio(world):
    victim = world.members[0]
    chal = gen_challenge(db, value=100)
    solve = gen_solve(db, user_id=victim.id, team_id=world.team.id, challenge_id=chal.id)
    score = Teams.query.get(world.team.id).get_score()
    r = remove(world.captain_client, victim.id)
    assert r.status_code == 303 and r.headers["Location"].endswith("/team")
    assert Users.query.get(victim.id).team_id is None
    assert Teams.query.get(world.team.id).get_score() == score, "TRP does not move"
    assert Solves.query.get(solve.id).team_id == world.team.id
    assert Removal.query.filter_by(team_id=world.team.id, user_id=victim.id, lifted_at=None).count() == 1
    assert flash(world.captain_client, FLASH_NOTICE_KEY) == "member-removed"
    kinds = {row.kind for row in Activity.query.all()}
    assert {"studio.remove", "studio.leave"} <= kinds
    line = Activity.query.filter_by(kind="studio.remove").one()
    assert line.actor_id == world.captain.id and line.team_id == world.team.id


def test_a_member_who_is_not_the_captain_cannot_remove_anybody(world):
    r = remove(world.member_client, world.members[1].id)
    assert r.status_code == 403
    assert Users.query.get(world.members[1].id).team_id == world.team.id and Removal.query.count() == 0


def test_without_the_page_token_nothing_happens(world):
    r = remove(world.captain_client, world.members[0].id, nonce=False)
    assert r.status_code == 403
    assert Users.query.get(world.members[0].id).team_id == world.team.id


def test_the_captain_cannot_remove_themself_and_is_told_why(world):
    r = remove(world.captain_client, world.captain.id)
    assert r.status_code == 303
    assert "yourself" in " ".join(flash(world.captain_client, FLASH_ERROR_KEY)["members"])
    assert Users.query.get(world.captain.id).team_id == world.team.id and Removal.query.count() == 0


def test_a_person_who_is_not_in_the_studio_is_refused_with_a_plain_400(world):
    other = gen_team(db, name="Beta", email="beta@example.com", member_count=2)
    stranger = Plain(sorted(other.members, key=lambda u: u.id)[0])
    assert remove(world.captain_client, stranger.id).status_code == 400
    assert remove(world.captain_client, 999999).status_code == 400
    assert Users.query.get(stranger.id).team_id == other.id


def test_a_third_removal_in_a_day_is_refused(world):
    for member in world.members[:2]:
        assert remove(world.captain_client, member.id).status_code == 303
    assert Removal.query.count() == 2
    r = remove(world.captain_client, world.members[2].id)
    assert r.status_code == 303
    assert "2 members a day" in " ".join(flash(world.captain_client, FLASH_ERROR_KEY)["members"])
    assert Users.query.get(world.members[2].id).team_id == world.team.id and Removal.query.count() == 2


def test_the_removal_route_has_no_get(world):
    assert world.captain_client.get(f"/team/members/{world.members[0].id}/remove").status_code in (404, 405), "no GET: removing is never done by a link"


# ------------------------------------------------------------------ a removed person cannot walk back in

def test_a_removed_person_with_an_old_invite_link_is_turned_away_and_the_refusal_is_recorded(world):
    victim = world.members[0]
    code = invite_code(world)
    assert remove(world.captain_client, victim.id).status_code == 303
    client = login_as_user(world.app, victim.name)
    for method in ("get", "post"):
        r = getattr(client, method)(f"/teams/invite?code={code}", **({"data": {"nonce": nonce_of(client)}} if method == "post" else {}))
        assert r.status_code == 403, method
    assert Users.query.get(victim.id).team_id is None, "still out"
    assert Activity.query.filter_by(kind="studio.join.refused").count() >= 1


def test_a_removed_person_cannot_use_the_studios_name_and_password_either(world):
    victim = world.members[0]
    assert remove(world.captain_client, victim.id).status_code == 303
    client = login_as_user(world.app, victim.name)
    r = client.post("/teams/join", data={"name": "Alpha", "password": "password", "nonce": nonce_of(client)})
    assert r.status_code == 403
    assert Users.query.get(victim.id).team_id is None


def test_a_removed_person_can_join_another_studio(world):
    victim = world.members[0]
    other = gen_team(db, name="Beta", email="beta@example.com", member_count=1)
    other_id = other.id
    assert remove(world.captain_client, victim.id).status_code == 303
    client = login_as_user(world.app, victim.name)
    r = client.post("/teams/join", data={"name": "Beta", "password": "password", "nonce": nonce_of(client)})
    assert r.status_code in (302, 303), r.get_data(as_text=True)[:300]
    assert Users.query.get(victim.id).team_id == other_id


def test_the_refusal_page_says_what_happened_in_plain_words(world):
    victim = world.members[0]
    code = invite_code(world)
    assert remove(world.captain_client, victim.id).status_code == 303
    client = login_as_user(world.app, victim.name)
    page = client.get(f"/teams/invite?code={code}").get_data(as_text=True)
    assert "removed from this studio" in page and "ask the crew" in page.lower()


# ------------------------------------------------------------------ the rename policy, on both ways in

def rename_form(client, name):
    return client.post("/team", data={"name": name, "country": "", "affiliation": "", "nonce": nonce_of(client)})


def rename_api(client, name):
    return client.patch("/api/v1/teams/me", json={"name": name})  # the test client sends the CSRF token itself


def test_the_first_rename_by_form_works_and_leaves_a_line_with_the_old_and_the_new_name(world):
    r = rename_form(world.captain_client, "Alpha Two")
    assert r.status_code == 303
    assert Teams.query.get(world.team.id).name == "Alpha Two"
    line = Activity.query.filter_by(kind="studio.rename").one()
    assert json.loads(line.detail) == {"old": "Alpha", "new": "Alpha Two"} and line.actor_id == world.captain.id


def test_a_second_rename_by_form_within_a_day_is_refused_and_the_name_stays(world):
    assert rename_form(world.captain_client, "Alpha Two").status_code == 303
    r = rename_form(world.captain_client, "Alpha Three")
    assert r.status_code == 303
    assert "once a day" in " ".join(flash(world.captain_client, FLASH_ERROR_KEY)["name"])
    assert Teams.query.get(world.team.id).name == "Alpha Two"
    assert Activity.query.filter_by(kind="studio.rename").count() == 1


def test_the_same_policy_holds_on_the_api_path_a_second_rename_is_refused_with_403(world):
    first = rename_api(world.captain_client, "Alpha Two")
    assert first.status_code == 200, first.get_data(as_text=True)[:400]
    assert Teams.query.get(world.team.id).name == "Alpha Two"
    r = rename_api(world.captain_client, "Alpha Three")
    assert r.status_code == 403
    body = r.get_json()
    assert body["success"] is False and "once a day" in body["errors"]["name"][0]
    assert Teams.query.get(world.team.id).name == "Alpha Two"


def test_a_rename_by_form_then_by_api_counts_as_two_renames_not_one_each(world):
    assert rename_form(world.captain_client, "Alpha Two").status_code == 303
    assert rename_api(world.captain_client, "Alpha Three").status_code == 403


def test_the_form_and_the_api_both_refuse_from_the_real_start(world):
    set_config("l3mon_rename_locked_from", "1800000000")  # long ago (epoch seconds) compared with the test clock
    set_config("l3mon_rename_locked_from", "1577836801")  # 2020: already passed
    assert rename_form(world.captain_client, "Alpha Two").status_code == 303
    assert "locked" in " ".join(flash(world.captain_client, FLASH_ERROR_KEY)["name"])
    assert rename_api(world.captain_client, "Alpha Two").status_code == 403
    assert Teams.query.get(world.team.id).name == "Alpha"


def test_the_form_and_the_api_both_refuse_after_the_first_solve(world):
    chal = gen_challenge(db)
    gen_solve(db, user_id=world.members[1].id, team_id=world.team.id, challenge_id=chal.id)
    assert rename_form(world.captain_client, "Alpha Two").status_code == 303
    assert "scored" in " ".join(flash(world.captain_client, FLASH_ERROR_KEY)["name"])
    assert rename_api(world.captain_client, "Alpha Two").status_code == 403
    assert Teams.query.get(world.team.id).name == "Alpha"


def test_a_refused_rename_is_recorded_so_the_crew_can_see_the_attempt(world):
    assert rename_api(world.captain_client, "Alpha Two").status_code == 200
    assert rename_api(world.captain_client, "Alpha Three").status_code == 403
    line = Activity.query.filter_by(kind="studio.rename.refused").one()
    assert json.loads(line.detail) == {"asked": "Alpha Three", "via": "api"} and line.actor_id == world.captain.id


def test_the_crew_can_rename_any_studio_at_any_time_through_the_admin_api(world):
    set_config("l3mon_rename_locked_from", "1577836801")
    chal = gen_challenge(db)
    gen_solve(db, user_id=world.members[1].id, team_id=world.team.id, challenge_id=chal.id)
    admin = login_as_user(world.app, "admin")
    r = admin.patch(f"/api/v1/teams/{world.team.id}", json={"name": "Chosen By Crew"})
    assert r.status_code == 200
    assert Teams.query.get(world.team.id).name == "Chosen By Crew"
    line = Activity.query.filter_by(kind="studio.rename").one()
    assert line.actor_name == "admin" and json.loads(line.detail)["new"] == "Chosen By Crew"


def test_a_member_who_is_not_the_captain_still_cannot_rename_at_all(world):
    assert rename_form(world.member_client, "Hijacked").status_code == 403
    assert Teams.query.get(world.team.id).name == "Alpha"
