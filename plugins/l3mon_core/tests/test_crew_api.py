"""The crew's side of the activity ledger and the removals: list removals, lift one, read the ledger, check the chain.

All under /api/v1/l3mon/admin/ (nginx treats that whole prefix as the administrator surface; CTFd's @admins_only checks the session or token as well).

Run through tools/run-ctfd-tests.sh:
    tools/run-ctfd-tests.sh l3mon/ctfd:dev -- -q -p no:randomly -p no:cacheprovider /l3mon_tests/l3mon_core
"""
import datetime

import pytest

from CTFd.models import Users, db
from CTFd.plugins.l3mon_core import activity, studio_rules
from CTFd.plugins.l3mon_core.models import Activity, Removal
from tests.helpers import create_ctfd, destroy_ctfd, gen_team, login_as_user

NOW = datetime.datetime(2026, 11, 20, 9, 0, 0)


class P:
    def __init__(self, row):
        self.id, self.name = row.id, row.name


@pytest.fixture()
def world():
    app = create_ctfd(enable_plugins=True, user_mode="teams")
    with app.app_context():
        team = gen_team(db, name="Alpha", email="alpha@example.com", member_count=4)
        users = sorted(team.members, key=lambda u: u.id)
        captain, victim, other = users[0], users[1], users[2]
        removal = studio_rules.remove_member(team, victim, captain, now=NOW)
        db.session.commit()
        w = type("W", (), {})()
        w.app, w.team, w.captain, w.victim, w.other, w.removal_id = app, P(team), P(captain), P(victim), P(other), removal.id
        # login_as_user opens an app context of its own and closes it: live rows would be detached afterwards, so only the plain copies above are used from here on
        w.admin = login_as_user(app, "admin")
        w.player = login_as_user(app, w.other.name)
        yield w
    destroy_ctfd(app)


def test_the_crew_sees_the_removals_with_who_removed_whom_and_when(world):
    r = world.admin.get("/api/v1/l3mon/admin/removals")
    assert r.status_code == 200
    rows = r.get_json()["data"]
    assert len(rows) == 1
    row = rows[0]
    assert (row["id"], row["team_id"], row["team_name"], row["user_id"], row["user_name"]) == (world.removal_id, world.team.id, "Alpha", world.victim.id, world.victim.name)
    assert row["removed_by"] == world.captain.id and row["lifted_at"] is None and row["recent_activity"] is False


def test_the_list_can_be_limited_to_one_studio_and_to_the_ones_still_in_force(world):
    other_team = gen_team(db, name="Beta", email="beta@example.com", member_count=2)
    assert world.admin.get(f"/api/v1/l3mon/admin/removals?team_id={other_team.id}").get_json()["data"] == []
    assert len(world.admin.get(f"/api/v1/l3mon/admin/removals?team_id={world.team.id}&active=1").get_json()["data"]) == 1


def test_lifting_a_removal_lets_the_person_join_again_and_is_recorded_with_who_lifted_it(world):
    r = world.admin.post(f"/api/v1/l3mon/admin/removals/{world.removal_id}/lift", json={})
    assert r.status_code == 200 and r.get_json()["success"] is True
    saved = Removal.query.get(world.removal_id)
    assert saved.lifted_at is not None and saved.lifted_by == Users.query.filter_by(name="admin").first().id
    assert studio_rules.joining_refusal(world.victim.id, world.team.id) is None
    line = Activity.query.filter_by(kind="studio.removal.lift").one()
    assert line.actor_name == "admin" and line.team_id == world.team.id and line.target == f"user:{world.victim.id}"
    assert len(world.admin.get("/api/v1/l3mon/admin/removals?active=1").get_json()["data"]) == 0


def test_lifting_twice_or_lifting_nothing_is_an_honest_error(world):
    assert world.admin.post(f"/api/v1/l3mon/admin/removals/{world.removal_id}/lift", json={}).status_code == 200
    assert world.admin.post(f"/api/v1/l3mon/admin/removals/{world.removal_id}/lift", json={}).status_code == 409
    assert world.admin.post("/api/v1/l3mon/admin/removals/99999/lift", json={}).status_code == 404


def test_a_player_and_a_visitor_get_nothing_of_it(world):
    visitor = world.app.test_client()
    for client, refused in ((world.player, (302, 403)), (visitor, (302, 403))):  # CTFd's admin guard sends anybody who is not an administrator to the sign-in page
        for method, url in (("get", "/api/v1/l3mon/admin/removals"), ("post", f"/api/v1/l3mon/admin/removals/{world.removal_id}/lift"),
                            ("get", "/api/v1/l3mon/admin/activity"), ("get", "/api/v1/l3mon/admin/activity/verify")):
            response = getattr(client, method)(url, **({"json": {}} if method == "post" else {}))
            assert response.status_code in refused, (method, url, response.status_code)
            assert b'"data"' not in response.data, "nothing of the ledger or the removals leaves"
    assert Removal.query.get(world.removal_id).lifted_at is None


def test_the_ledger_is_read_newest_first_with_the_detail_as_data(world):
    rows = world.admin.get("/api/v1/l3mon/admin/activity").get_json()["data"]
    assert rows, "the removal and its leave line are there"
    ids = [row["id"] for row in rows]
    assert ids == sorted(ids, reverse=True)
    removal = next(row for row in rows if row["kind"] == "studio.remove")
    assert removal["detail"]["name"] == world.victim.name and removal["actor_id"] == world.captain.id
    assert set(removal) >= {"id", "at", "kind", "actor_id", "actor_name", "team_id", "target", "detail", "ip", "user_agent", "request_id"}
    assert "hash" not in removal and "prev_hash" not in removal, "the chain is for the verifier, not for the reader"


def test_the_ledger_can_be_filtered_by_studio_person_and_kind_and_has_a_limit(world):
    base = "/api/v1/l3mon/admin/activity"
    assert {r["kind"] for r in world.admin.get(f"{base}?kind=studio.remove").get_json()["data"]} == {"studio.remove"}
    assert all(r["team_id"] == world.team.id for r in world.admin.get(f"{base}?team_id={world.team.id}").get_json()["data"])
    assert world.admin.get(f"{base}?team_id=999999").get_json()["data"] == []
    assert len(world.admin.get(f"{base}?limit=1").get_json()["data"]) == 1
    assert world.admin.get(f"{base}?limit=abc").status_code == 400
    assert len(world.admin.get(f"{base}?limit=100000").get_json()["data"]) <= 500, "an absurd limit is clipped, not obeyed"


def test_the_crew_can_check_the_chain_and_see_whether_the_database_refuses_changes(world):
    body = world.admin.get("/api/v1/l3mon/admin/activity/verify").get_json()["data"]
    assert body["ok"] is True and body["checked"] >= 2 and body["first_bad_id"] is None
    assert body["triggers_installed"] is True
