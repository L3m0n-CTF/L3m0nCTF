"""What participants do at the door and in their account is written to the activity ledger (docs/runbooks/17-activity-log.md).

Sign-in and sign-out are seen as the SESSION changing (so a sign-in with Google or CTFtime counts exactly like one with a password); a refused sign-in is the sign-in form
coming back with errors. The rest is a small table of (endpoint, what happened) in plugins/l3mon_core/activity_hooks.py, tested here as a plain function and through real
requests. A ledger problem never changes the answer a player gets. Nothing secret is ever written: not a password, not a token, not the text of a flag.

Run through tools/run-ctfd-tests.sh:
    tools/run-ctfd-tests.sh l3mon/ctfd:dev -- -q -p no:randomly -p no:cacheprovider /l3mon_tests/l3mon_core
"""
import json
import re

import pytest

from CTFd.models import Users, db
from CTFd.plugins.l3mon_core import activity, activity_hooks
from CTFd.plugins.l3mon_core.models import Activity
from tests.helpers import create_ctfd, destroy_ctfd, gen_team, gen_user, login_as_user, register_user


def nonce_of(client):
    with client.session_transaction() as sess:
        return sess.get("nonce")


def kinds():
    return [row.kind for row in Activity.query.order_by(Activity.id).all()]


def line(kind):
    return Activity.query.filter_by(kind=kind).order_by(Activity.id.desc()).first()


@pytest.fixture()
def app():
    app = create_ctfd(enable_plugins=True, user_mode="teams")
    yield app
    destroy_ctfd(app)


# ------------------------------------------------------------------ signing in and out

def test_a_sign_in_leaves_a_line_with_the_person_the_address_and_the_browser(app):
    with app.app_context():
        gen_user(db, name="player_one", email="one@example.com", verified=True)
        with app.test_client() as client:
            client.get("/login")
            r = client.post("/login", data={"name": "player_one", "password": "password", "nonce": nonce_of(client)},
                            headers={"User-Agent": "TestBrowser/1.0"}, environ_overrides={"REMOTE_ADDR": "203.0.113.50"})
            assert r.status_code == 302
        row = line("auth.login.ok")
        assert row is not None and row.actor_name == "player_one" and row.ip == "203.0.113.50" and row.user_agent == "TestBrowser/1.0"
        assert json.loads(row.detail)["via"] == "auth.login"


def test_a_wrong_password_leaves_a_failure_line_naming_the_account_that_was_attacked_and_never_the_password(app, capsys):
    with app.app_context():
        two = gen_user(db, name="player_two", email="two@example.com", verified=True)
        two_id = two.id
        baseline = Activity.query.count()  # CTFd's own set-up signs the first administrator in, which is (rightly) a line too
        with app.test_client() as client:
            client.get("/login")
            capsys.readouterr()
            r = client.post("/login", data={"name": "player_two", "password": "hunter2-wrong", "nonce": nonce_of(client)})
            assert r.status_code == 200, "CTFd shows the form again"
        row = line("auth.login.fail")
        assert row is not None and json.loads(row.detail) == {"account": two_id, "by": "name"} and row.target == f"user:{two_id}"
        assert "player_two" not in row.detail, "the typed text itself is not kept: the account is named by its number"
        assert row.actor_id is None, "nobody was signed in"
        assert "hunter2-wrong" not in row.detail and "hunter2-wrong" not in capsys.readouterr().out
        assert "auth.login.ok" not in kinds()[baseline:]


def test_a_name_that_matches_no_account_is_recorded_as_a_keyed_hash_never_as_the_text(app, capsys):
    """People type an e-mail address, or even their password, into the name box by mistake. Whatever does not match an account is kept only as a keyed hash: the same
    string can be recognised the next time (a guessing run), but it can never be read, and a weak string cannot be guessed back without the platform's secret key."""
    with app.app_context():
        with app.test_client() as client:
            client.get("/login")
            capsys.readouterr()
            for typed in ("nobody_here", "nobody_here", "My-Real-Password-9"):
                client.post("/login", data={"name": typed, "password": "x", "nonce": nonce_of(client)})
        rows = Activity.query.filter_by(kind="auth.login.fail").order_by(Activity.id).all()
        details = [json.loads(r.detail) for r in rows]
        assert all(d["unknown"] is True and d["shape"] == "name" and re.fullmatch(r"[0-9a-f]{12}", d["typed"]) for d in details)
        assert details[0]["typed"] == details[1]["typed"] != details[2]["typed"], "the same text gives the same mark, another text another"
        everything = " ".join(r.detail for r in rows) + capsys.readouterr().out
        assert "nobody_here" not in everything and "My-Real-Password-9" not in everything


def test_an_address_typed_into_the_name_box_matches_its_account_or_leaves_only_its_domain(app):
    with app.app_context():
        three = gen_user(db, name="player_six", email="six@example.com", verified=True)
        six_id = three.id
        with app.test_client() as client:
            client.get("/login")
            client.post("/login", data={"name": "six@example.com", "password": "wrong", "nonce": nonce_of(client)})
            client.post("/login", data={"name": "stranger@example.org", "password": "wrong", "nonce": nonce_of(client)})
        known, unknown = Activity.query.filter_by(kind="auth.login.fail").order_by(Activity.id).all()
        assert json.loads(known.detail) == {"account": six_id, "by": "email"}
        stranger = json.loads(unknown.detail)
        assert stranger["unknown"] is True and stranger["shape"] == "email" and stranger["email_domain"] == "example.org"
        assert "stranger" not in unknown.detail and "six@example.com" not in known.detail


def test_signing_out_leaves_a_line_and_a_page_view_leaves_none(app):
    with app.app_context():
        gen_user(db, name="player_three", email="three@example.com", verified=True)
        client = login_as_user(app, "player_three")
        before = len(kinds())
        client.get("/team")
        client.get("/settings")
        assert len(kinds()) == before, "looking at pages writes nothing"
        client.post("/logout", data={"nonce": nonce_of(client)})
        assert kinds()[before:] == ["auth.logout"]
        assert line("auth.logout").actor_name == "player_three"


def test_a_sign_in_that_changes_the_person_is_a_sign_in_not_a_second_one_for_every_request(app):
    with app.app_context():
        gen_user(db, name="player_four", email="four@example.com", verified=True)
        baseline = Activity.query.count()
        client = login_as_user(app, "player_four")
        for _ in range(3):
            client.get("/team")
        assert kinds()[baseline:].count("auth.login.ok") == 1


# ------------------------------------------------------------------ registration

def test_a_registration_leaves_one_line_with_the_new_account(app):
    with app.app_context():
        register_user(app, name="newcomer", email="newcomer@example.com")
        row = line("auth.register")
        assert row is not None and row.actor_name == "newcomer"
        assert json.loads(row.detail)["email_domain"] == "example.com"
        assert "newcomer@example.com" not in row.detail, "the address stays in the account, the ledger holds only its domain"


def test_a_refused_registration_leaves_a_failure_line_with_what_was_tried(app):
    with app.app_context():
        gen_user(db, name="taken_name", email="taken@example.com")
        with app.test_client() as client:
            client.get("/register")
            r = client.post("/register", data={"name": "taken_name", "email": "other@example.com", "password": "password", "nonce": nonce_of(client)})
            assert r.status_code == 200
        row = line("auth.register.fail")
        assert row is not None and json.loads(row.detail)["name"] == "taken_name"


# ------------------------------------------------------------------ resend, confirmation, reset

def test_asking_for_the_confirmation_mail_again_is_recorded(app):
    with app.app_context():
        gen_user(db, name="unverified", email="unverified@example.com", verified=False)
        client = login_as_user(app, "unverified")
        client.post("/confirm", data={"nonce": nonce_of(client)})
        row = line("auth.confirm.resend")
        assert row is not None and row.actor_name == "unverified"


def test_a_password_reset_request_is_recorded_with_the_address_domain_only(app):
    with app.app_context():
        gen_user(db, name="forgetful", email="forgetful@example.com", verified=True)
        with app.test_client() as client:
            client.get("/reset_password")
            client.post("/reset_password", data={"email": "forgetful@example.com", "nonce": nonce_of(client)})
        row = line("auth.reset.request")
        assert row is not None and json.loads(row.detail) == {"email_domain": "example.com"}
        assert "forgetful@example.com" not in row.detail


# ------------------------------------------------------------------ the account page

def test_changing_the_account_records_which_fields_changed_and_never_their_values(app, capsys):
    with app.app_context():
        gen_user(db, name="settler", email="settler@example.com", verified=True)
        client = login_as_user(app, "settler")
        capsys.readouterr()
        r = client.post("/settings", data={"nonce": nonce_of(client), "part": "details", "name": "settler_new"})
        assert r.status_code == 303
        row = line("account.update")
        assert row is not None and json.loads(row.detail) == {"fields": ["name"]}
        assert "settler_new" not in row.detail, "the new name is in the account, not copied into the ledger"


# ------------------------------------------------------------------ the table itself, as a plain function (the rest of the flows need a running challenge host)

def describe(endpoint, method="POST", status=200, args=None, form=None, body=None, before=None, after=None):
    return activity_hooks.describe(endpoint=endpoint, method=method, status=status, view_args=args or {}, form=form or {}, body=body, uid_before=before, uid_after=after)


def test_a_hint_unlock_is_recorded_with_what_was_unlocked(app):
    events = describe("api.unlocks_unlock_list", body={"target": 7, "type": "hints"}, before=5, after=5)
    assert [(e["kind"], e["target"], e["detail"]) for e in events] == [("hint.unlock", "hint:7", {})]
    assert describe("api.unlocks_unlock_list", status=400, body={"target": 7, "type": "hints"}, before=5, after=5) == [], "a refused unlock is not an unlock"


def test_the_instance_actions_are_recorded_start_renew_stop_and_a_refusal(app):
    start = describe("l3mon_instances.start_instance", status=200, body={"challenge_id": 12}, before=5, after=5)
    renew = describe("l3mon_instances.renew_instance", args={"challenge_id_param": "12"}, status=200, before=5, after=5)
    stop = describe("l3mon_instances.stop_instance", method="DELETE", args={"challenge_id_param": "12"}, status=200, before=5, after=5)
    refused = describe("l3mon_instances.start_instance", status=429, body={"challenge_id": 12}, before=5, after=5)
    assert [e["kind"] for e in start + renew + stop + refused] == ["instance.start", "instance.renew", "instance.stop", "instance.start.refused"]
    assert all(e["target"] == "challenge:12" for e in start + renew + stop + refused)
    assert refused[0]["detail"] == {"status": 429}


def test_an_endpoint_that_is_not_in_the_table_writes_nothing(app):
    assert describe("views.static_html", method="GET", before=5, after=5) == []
    assert describe("api.challenges_challenge_attempt", body={"challenge_id": 1, "submission": "anything"}, before=5, after=5) == [], "flag attempts stay in CTFd's own submissions table"


def test_the_crew_audit_lines_are_mirrored_into_the_ledger_so_there_is_one_timeline(app):
    from CTFd.plugins.l3mon_core import audit

    with app.app_context():
        admin = Users.query.filter_by(name="admin").first()
        audit.record("release.set", "programme:alpha", "withheld -> released", actor=admin)
        db.session.commit()
        row = line("crew.release.set")
        assert row is not None and row.actor_name == "admin" and row.target == "programme:alpha"
        assert json.loads(row.detail) == {"detail": "withheld -> released"}


def test_a_ledger_that_cannot_be_written_never_changes_the_answer_to_the_player(app, monkeypatch):
    with app.app_context():
        gen_user(db, name="player_five", email="five@example.com", verified=True)
        monkeypatch.setattr(activity, "_write", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("database gone")))
        with app.test_client() as client:
            client.get("/login")
            r = client.post("/login", data={"name": "player_five", "password": "password", "nonce": nonce_of(client)})
            assert r.status_code == 302, "the sign-in still works"


def test_changing_the_own_account_through_the_api_records_the_field_names_and_never_the_values(app):
    events = describe("api.users_user_private", method="PATCH", body={"name": "newname", "password": "hunter2", "confirm": "hunter2"}, before=5, after=5)
    assert [(e["kind"], e["detail"]) for e in events] == [("account.update", {"fields": ["name", "password"], "via": "api"})]
    assert "hunter2" not in json.dumps(events) and "newname" not in json.dumps(events)
