"""The footer of every page shows the organisers' address and the Discord invitation that event.toml names ([contact]), and nothing when it names none.

The two settings reach the platform as L3MON_CONTACT_EMAIL and L3MON_DISCORD_URL (the compose environment). Until they are set the footer said
"Contact: announced soon" on every page of a live event, which looks unfinished. A value that is not a plain address (it goes into a mailto link and a
link that opens a new tab) is ignored, never printed.

Run through tools/run-ctfd-tests.sh with the working-tree theme and plugins mounted (see docs/deploy/theme-slices.md).
"""
import re

import pytest

from tests.helpers import create_ctfd, destroy_ctfd, login_as_user, register_user

PUBLIC_PAGES = ("/login", "/register", "/reset_password")
EMAIL = "crew@example.org"
DISCORD = "https://discord.gg/AbC123"


@pytest.fixture
def app(monkeypatch):
    monkeypatch.delenv("L3MON_CONTACT_EMAIL", raising=False)
    monkeypatch.delenv("L3MON_DISCORD_URL", raising=False)
    app = create_ctfd(enable_plugins=True, ctf_theme="l3mon", user_mode="teams", ctf_name="L3m0n test event")
    yield app
    destroy_ctfd(app)


def footer_line(html):
    found = re.search(r'<p class="foot-small">(.*?)</p>', html, re.S)
    assert found, "the page has no footer small print"
    return found.group(1)


def test_with_nothing_set_the_footer_says_so_and_has_no_discord_link(app):
    with app.test_client() as client:
        for path in PUBLIC_PAGES:
            line = footer_line(client.get(path).get_data(as_text=True))
            assert "Contact: announced soon" in line and "Discord" not in line, (path, line)


def test_the_address_and_the_invitation_are_shown_on_every_public_page(app, monkeypatch):
    monkeypatch.setenv("L3MON_CONTACT_EMAIL", EMAIL)
    monkeypatch.setenv("L3MON_DISCORD_URL", DISCORD)
    with app.test_client() as client:
        for path in PUBLIC_PAGES:
            line = footer_line(client.get(path).get_data(as_text=True))
            assert f'Contact: <a href="mailto:{EMAIL}">{EMAIL}</a>' in line, (path, line)
            assert f'<a href="{DISCORD}" rel="noopener" target="_blank">Discord</a>' in line, (path, line)
            assert "announced soon" not in line


def test_a_signed_in_page_and_the_studio_pages_show_them_too(app, monkeypatch):
    monkeypatch.setenv("L3MON_CONTACT_EMAIL", EMAIL)
    monkeypatch.setenv("L3MON_DISCORD_URL", DISCORD)
    register_user(app)
    with login_as_user(app) as client:
        for path in ("/settings", "/teams/new", "/teams/join"):
            answer = client.get(path)
            assert answer.status_code == 200, path
            line = footer_line(answer.get_data(as_text=True))
            assert EMAIL in line and DISCORD in line, (path, line)


def test_an_address_alone_shows_the_address_and_an_invitation_alone_leaves_the_contact_unsaid(app, monkeypatch):
    monkeypatch.setenv("L3MON_CONTACT_EMAIL", EMAIL)
    with app.test_client() as client:
        line = footer_line(client.get("/login").get_data(as_text=True))
        assert EMAIL in line and "Discord" not in line
    monkeypatch.delenv("L3MON_CONTACT_EMAIL")
    monkeypatch.setenv("L3MON_DISCORD_URL", DISCORD)
    with app.test_client() as client:
        line = footer_line(client.get("/login").get_data(as_text=True))
        assert "Contact: announced soon" in line and DISCORD in line


@pytest.mark.parametrize("bad_email", ['x"><script>alert(1)</script>@example.org', "two@example.org three@example.org", "no-at-sign", "a@b", "x@example.org\nBcc: y@example.org"])
def test_a_setting_that_is_not_a_plain_address_is_ignored(app, monkeypatch, bad_email):
    monkeypatch.setenv("L3MON_CONTACT_EMAIL", bad_email)
    with app.test_client() as client:
        html = client.get("/login").get_data(as_text=True)
    assert "Contact: announced soon" in footer_line(html)
    assert "<script>alert" not in html and "Bcc:" not in html


@pytest.mark.parametrize("bad_url", ["http://discord.gg/abc", "https://example.org/abc", 'https://discord.gg/abc"onmouseover="x', "javascript:alert(1)", "https://discord.gg/"])
def test_an_invitation_that_is_not_a_discord_address_is_ignored(app, monkeypatch, bad_url):
    monkeypatch.setenv("L3MON_DISCORD_URL", bad_url)
    with app.test_client() as client:
        html = client.get("/login").get_data(as_text=True)
    assert "Discord" not in footer_line(html)
    assert "onmouseover" not in html and "javascript:alert" not in html
