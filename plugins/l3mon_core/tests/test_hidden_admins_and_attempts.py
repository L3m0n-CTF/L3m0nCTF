"""The crew's accounts are hidden from players, and a flag attempt must be text of a sensible length (hidden_admins.py, attempt_input.py).

Run through tools/run-ctfd-tests.sh:
    tools/run-ctfd-tests.sh l3mon/ctfd:dev -- -q -p no:randomly -p no:cacheprovider /l3mon_tests/l3mon_core
"""
import pytest

from CTFd.models import Users, db
from CTFd.plugins.l3mon_core import hidden_admins
from CTFd.plugins.l3mon_core.attempt_input import MAX_FLAG_LENGTH
from tests.helpers import create_ctfd, destroy_ctfd, gen_challenge, gen_flag, gen_user, login_as_user, register_user


def test_an_administrator_is_saved_hidden_however_it_was_made():
    app = create_ctfd(enable_plugins=True)
    with app.app_context():
        made = gen_user(db, name="crew-one", email="crew1@example.com", type="admin")  # made as an administrator, hidden not asked for
        assert made.hidden is True
        player = gen_user(db, name="player-one", email="p1@example.com")
        assert player.hidden is False
        player.type = "admin"  # promoted later
        db.session.commit()
        assert Users.query.filter_by(name="player-one").first().hidden is True
    destroy_ctfd(app)


def test_administrators_that_already_exist_are_hidden_at_start():
    app = create_ctfd(enable_plugins=True)
    with app.app_context():
        # an account saved before this change, by a path that did not hide it (written around the mapper)
        db.session.execute(Users.__table__.insert().values(name="old-admin", email="old@example.com", password="x", type="admin", hidden=False, banned=False, verified=True))
        db.session.commit()
        assert Users.query.filter_by(name="old-admin").first().hidden is False
        assert hidden_admins.hide_existing_admins() == 1
        assert Users.query.filter_by(name="old-admin").first().hidden is True
        assert hidden_admins.hide_existing_admins() == 0
    destroy_ctfd(app)


def test_a_player_cannot_find_the_crew_account_in_the_user_list():
    app = create_ctfd(enable_plugins=True)
    with app.app_context():
        gen_user(db, name="crew-two", email="crew2@example.com", type="admin")
        register_user(app, name="ravi", email="ravi@example.com")
        player = login_as_user(app, "ravi")
        names = [u["name"] for u in player.get("/api/v1/users").get_json()["data"]]
        assert "crew-two" not in names and "ravi" in names
        crew_id = Users.query.filter_by(name="crew-two").first().id
        assert player.get(f"/api/v1/users/{crew_id}").status_code == 404
    destroy_ctfd(app)


@pytest.fixture()
def attempt_world():
    app = create_ctfd(enable_plugins=True)
    with app.app_context():
        challenge = gen_challenge(db, value=100)
        challenge_id = challenge.id
        gen_flag(db, challenge_id, content="wombat-ok")
        register_user(app, name="ravi", email="ravi@example.com")
        client = login_as_user(app, "ravi")

        def attempt(submission="__missing__", raw=None):
            body = {"challenge_id": challenge_id}
            if submission != "__missing__":
                body["submission"] = submission
            return client.post("/api/v1/challenges/attempt", json=body if raw is None else raw)

        yield attempt
    destroy_ctfd(app)


@pytest.mark.parametrize("bad", ["__missing__", None, 123, ["a"], {"a": 1}, True])
def test_an_attempt_that_is_not_text_is_refused_with_400_not_a_server_error(attempt_world, bad):
    r = attempt_world(bad)
    assert r.status_code == 400, r.get_data(as_text=True)
    body = r.get_json()
    assert body["success"] is False and body["error"] == "invalid_submission"


def test_an_attempt_that_is_too_long_is_refused_and_one_at_the_limit_is_judged(attempt_world):
    assert attempt_world("a" * (MAX_FLAG_LENGTH + 1)).status_code == 400
    ok = attempt_world("a" * MAX_FLAG_LENGTH)
    assert ok.status_code == 200 and ok.get_json()["data"]["status"] == "incorrect"


def test_a_normal_wrong_and_a_right_attempt_are_still_judged(attempt_world):
    assert attempt_world("wombat-nope").get_json()["data"]["status"] == "incorrect"
    assert attempt_world("wombat-ok").get_json()["data"]["status"] == "correct"


def test_a_json_list_as_the_whole_body_is_refused(attempt_world):
    assert attempt_world(raw=["wombat-ok"]).status_code == 400
