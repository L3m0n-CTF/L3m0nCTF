"""Joining a studio and unlocking a hint take the studio's row lock first (see player_locks.py).

SQLite, which these tests run on, has one writer at a time and ignores a row lock, so the lock itself cannot be seen here. What can
be seen, and what a regression would break, is WHICH studio is locked and for which requests, and that the lock is taken before CTFd's
own handler runs. The real concurrency (nine simultaneous joins, three simultaneous unlocks) is proved against MariaDB by
tests/integration/test_races.py.

Run through tools/run-ctfd-tests.sh:
    tools/run-ctfd-tests.sh l3mon/ctfd:dev -- -q -p no:randomly -p no:cacheprovider /l3mon_tests/l3mon_core
"""
import pytest

from CTFd.models import Teams, Unlocks, Users, db
from CTFd.plugins.l3mon_core import player_locks
from CTFd.utils import set_config
from tests.helpers import create_ctfd, destroy_ctfd, gen_challenge, gen_hint, login_as_user, register_user


def nonce_of(client):
    with client.session_transaction() as sess:
        return sess.get("nonce")


@pytest.fixture()
def world(monkeypatch):
    """Teams mode, two studios of one member each, a free hint, and a record of every lock the plugin takes. The record notes
    the number of members the studio had when the lock was taken, so a test can tell that the lock came before the handler's write."""
    app = create_ctfd(enable_plugins=True, user_mode="teams")
    taken = []
    real = player_locks.lock_studio

    def spy(team_id):
        real(team_id)
        taken.append((team_id, db.session.query(Users).filter(Users.team_id == team_id).count()))

    monkeypatch.setattr(player_locks, "lock_studio", spy)
    with app.app_context():
        set_config("team_size", 3)
        register_user(app, name="owner", email="owner@example.com")
        client = login_as_user(app, "owner")
        client.get("/team")
        r = client.post("/teams/new", data={"name": "Alpha Studio", "password": "password", "nonce": nonce_of(client)})
        assert r.status_code == 302
        alpha = Teams.query.filter_by(name="Alpha Studio").first()
        challenge = gen_challenge(db, value=100)
        free_hint = gen_hint(db, challenge.id, content="a clue", cost=0)
        costly_hint = gen_hint(db, challenge.id, content="a dear clue", cost=50)
        taken.clear()
        yield app, SimpleWorld(alpha_id=alpha.id, owner_id=Users.query.filter_by(name="owner").first().id, free=free_hint.id, costly=costly_hint.id, taken=taken)
    destroy_ctfd(app)


class SimpleWorld:
    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)


def new_player(app, name):
    register_user(app, name=name, email=f"{name}@example.com")
    client = login_as_user(app, name)
    client.get("/team")
    return client


def test_a_join_with_the_studios_name_locks_that_studio(world):
    app, w = world
    client = new_player(app, "joiner")
    r = client.post("/teams/join", data={"name": "Alpha Studio", "password": "password", "nonce": nonce_of(client)})
    assert r.status_code == 302
    # locked once, for the studio that was named, and while it still had its one member (before the handler wrote the second)
    assert w.taken == [(w.alpha_id, 1)]


def test_a_join_with_a_wrong_name_locks_nothing_and_is_still_refused(world):
    app, w = world
    client = new_player(app, "joiner")
    r = client.post("/teams/join", data={"name": "No Such Studio", "password": "password", "nonce": nonce_of(client)})
    assert r.status_code == 200 and "That information is incorrect" in r.get_data(as_text=True)
    assert w.taken == []


def test_an_invite_accepted_locks_the_invited_studio(world):
    app, w = world
    owner = login_as_user(app, "owner")
    alpha = Teams.query.get(w.alpha_id)
    code = alpha.get_invite_code()
    client = new_player(app, "invited")
    r = client.post(f"/teams/invite?code={code}", data={"nonce": nonce_of(client)})
    assert r.status_code == 302
    assert w.taken == [(w.alpha_id, 1)]
    assert owner is not None


def test_a_bad_invite_locks_nothing_and_is_still_explained(world):
    app, w = world
    client = new_player(app, "invited")
    r = client.post("/teams/invite?code=not-a-code", data={"nonce": nonce_of(client)})
    assert r.status_code == 403
    assert w.taken == []


def test_reading_the_join_or_invite_page_locks_nothing(world):
    app, w = world
    client = new_player(app, "reader")
    code = Teams.query.get(w.alpha_id).get_invite_code()
    assert client.get("/teams/join").status_code == 200
    assert client.get(f"/teams/invite?code={code}").status_code == 200
    assert w.taken == []


def test_a_hint_unlock_locks_the_studio_of_the_player(world):
    app, w = world
    client = login_as_user(app, "owner")
    r = client.post("/api/v1/unlocks", json={"target": w.free, "type": "hints"})
    assert r.status_code == 200, r.get_data(as_text=True)
    assert w.taken == [(w.alpha_id, 1)]
    assert Unlocks.query.filter_by(target=w.free).count() == 1


def test_an_unlock_by_a_player_without_a_studio_is_refused_plainly_not_with_a_server_error(world):
    app, w = world
    client = new_player(app, "loner")
    r = client.post("/api/v1/unlocks", json={"target": w.free, "type": "hints"})
    assert r.status_code == 403, r.status_code
    body = r.get_json()
    assert body["success"] is False and body["error"] == "no_team"
    assert Unlocks.query.count() == 0
    assert w.taken == []


def test_an_unlock_of_a_dear_hint_still_costs_once_and_a_second_try_is_refused(world):
    app, w = world
    from CTFd.models import Awards

    challenge_award = Awards(team_id=w.alpha_id, user_id=w.owner_id, name="bonus", value=100)
    db.session.add(challenge_award)
    db.session.commit()
    client = login_as_user(app, "owner")
    first = client.post("/api/v1/unlocks", json={"target": w.costly, "type": "hints"})
    second = client.post("/api/v1/unlocks", json={"target": w.costly, "type": "hints"})
    assert (first.status_code, second.status_code) == (200, 400)
    assert Unlocks.query.filter_by(target=w.costly).count() == 1
    assert Teams.query.get(w.alpha_id).get_score(admin=True) == 50


def test_other_posts_and_other_endpoints_are_not_locked(world):
    app, w = world
    client = login_as_user(app, "owner")
    assert client.get("/team").status_code == 200
    assert client.get("/settings").status_code in (200, 302, 303)
    assert w.taken == []


def test_the_crew_is_not_held_up_by_the_lock(world):
    app, w = world
    admin = login_as_user(app, "admin")
    admin.get("/team")
    r = admin.post("/teams/join", data={"name": "Alpha Studio", "password": "wrong", "nonce": nonce_of(admin)})
    assert r.status_code == 200 and "That information is incorrect" in r.get_data(as_text=True)
    assert w.taken == []


def test_the_hook_is_registered_once_even_when_installed_twice():
    app = create_ctfd(enable_plugins=True, user_mode="teams")
    with app.app_context():
        before = len(app.before_request_funcs[None])
        player_locks.install(app)
        assert len(app.before_request_funcs[None]) == before
    destroy_ctfd(app)
