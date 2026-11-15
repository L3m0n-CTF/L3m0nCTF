"""The Sign out button works, and a form that was sent without its script does not end on a "not found" page (found on 2026-11-14 by the superior's first test).

The theme's Sign out is a form that POSTs the page's nonce to /logout (so another site cannot sign a player out); CTFd answers /logout only for GET, and its CSRF
check turned the POST into a "not found". The flag form of the board posts to /board when its script has not taken over.

Run through tools/run-ctfd-tests.sh with the working-tree theme and plugins mounted (see docs/deploy/theme-slices.md).
"""
import re

import pytest

from tests.helpers import create_ctfd, destroy_ctfd, login_as_user, register_user

SIGN_OUT_FORM = re.compile(r'<form class="osd-out" method="post" action="/logout"><input type="hidden" name="nonce" value="([^"]+)"')


@pytest.fixture
def app():
    app = create_ctfd(enable_plugins=True, ctf_theme="l3mon", user_mode="teams", ctf_name="L3m0n test event")
    yield app
    destroy_ctfd(app)


def signed_in(client) -> bool:
    return client.get("/api/v1/users/me").status_code == 200


def test_the_sign_out_button_of_the_page_signs_the_player_out(app):
    register_user(app)
    with login_as_user(app) as client:
        assert signed_in(client)
        page = client.get("/settings").get_data(as_text=True)
        form = SIGN_OUT_FORM.search(page)
        assert form, "the page has no Sign out form: the test is looking at the wrong page"
        answer = client.post("/logout", data={"nonce": form.group(1)})
        assert answer.status_code in (302, 303), answer.status_code
        assert answer.headers["Location"].rstrip("/").endswith("") and "/logout" not in answer.headers["Location"]
        assert not signed_in(client), "the player is still signed in after pressing Sign out"
        assert client.get("/settings").status_code in (302, 403), "the settings page still opens after Sign out"


def test_sign_out_without_the_pages_nonce_does_nothing(app):
    register_user(app)
    with login_as_user(app) as client:
        assert client.post("/logout", data={"nonce": "not-the-nonce"}).status_code == 403
        assert client.post("/logout").status_code == 403
        assert signed_in(client), "a request without the right nonce must not sign anybody out (another site could do it)"


def test_a_visitor_who_posts_to_sign_out_is_refused_and_not_told_the_page_is_missing(app):
    with app.test_client() as client:
        assert client.post("/logout", data={"nonce": "x"}).status_code == 403


def test_the_old_address_of_sign_out_still_works(app):
    register_user(app)
    with login_as_user(app) as client:
        assert signed_in(client)
        assert client.get("/logout").status_code == 302
        assert not signed_in(client)


def test_the_flag_form_sent_without_its_script_goes_back_to_the_board_and_not_to_a_missing_page(app):
    register_user(app)
    with login_as_user(app) as client:
        with client.session_transaction() as sess:
            nonce = sess["nonce"]
        answer = client.post("/board", data={"nonce": nonce, "submission": "L3m0n{x}"})
        assert answer.status_code == 303, answer.status_code
        assert answer.headers["Location"].endswith("/board")
        assert client.post("/board", data={"submission": "L3m0n{x}"}).status_code == 403  # no nonce: refused as everywhere


def test_privacy_and_rules_lead_to_the_websites_pages_and_the_icon_address_to_the_icon(app):
    """The registration form links to /privacy (a 404 before 2026-11-15); the pages live on the website."""
    with app.test_client() as client:
        for page in ("privacy", "rules"):
            answer = client.get(f"/{page}")
            assert answer.status_code == 302 and answer.headers["Location"] == f"https://l3m0nctf.xyz/{page}/", (page, answer.status_code, answer.headers.get("Location"))
        icon = client.get("/favicon.ico")
        assert icon.status_code == 302 and icon.headers["Location"].endswith("/favicon.svg")
        assert client.get("/favicon.svg").status_code == 200


def test_a_bad_invite_link_is_explained_and_not_called_crew_only(app):
    """CTFd answers a mistyped or expired invite with 403; the theme said 'This area is for the crew only' and offered no way forward (found 2026-11-15)."""
    register_user(app)
    with login_as_user(app) as client:
        answer = client.get("/teams/invite?code=not-a-real-code")
        text = answer.get_data(as_text=True)
        assert answer.status_code == 403
        assert "That invite link did not work" in text and "Ask your captain for a fresh one" in text
        assert "for the crew only" not in text
        assert 'href="/teams/join"' in text, "the page must lead on to the join step"
        admin_page = client.get("/admin")  # an ordinary player on a crew page still gets the crew wording
        assert "for the crew only" in admin_page.get_data(as_text=True) or admin_page.status_code in (302, 403)
