"""What the pages offer must be something the server answers (found on 2026-11-14: the Sign out button was a POST form to /logout, CTFd answers /logout
only for GET, so the platform showed its "not found" page and the player stayed signed in; nothing had ever clicked the button).

The theme's pages are generated (ui/theme/l3mon/templates/_*.html, static/*.js). These tests read what they offer and ask the application's own route table
whether each address is answered, for the method the page uses: every form (its action and method), every link to an address of ours, and every address a
script of the theme calls. A page that offers something the server does not answer fails here, not in front of a player.

Run through tools/run-ctfd-tests.sh with the working-tree theme and plugins mounted (see docs/deploy/theme-slices.md).
"""
import re
from pathlib import Path

import pytest
from werkzeug.exceptions import MethodNotAllowed, NotFound
from werkzeug.routing import RequestRedirect

from tests.helpers import create_ctfd, destroy_ctfd


@pytest.fixture
def app():
    app = create_ctfd(enable_plugins=True, ctf_theme="l3mon", user_mode="teams", ctf_name="L3m0n test event")
    yield app
    destroy_ctfd(app)


def theme_dir(app) -> Path:
    return Path(app.root_path) / "themes" / "l3mon"


def answered(app, path: str, method: str) -> bool:
    """True when the application's route table has a view for this address and method (a redirect to the canonical address counts)."""
    adapter = app.url_map.bind("localhost")
    try:
        adapter.match(path, method=method)
        return True
    except RequestRedirect:
        return True
    except (NotFound, MethodNotAllowed):
        return False


def attribute(tag: str, name: str):
    found = re.search(rf'\b{name}\s*=\s*"([^"]*)"', tag, re.I)
    return found.group(1) if found else None


def own_address(value):
    """A literal address of the platform itself (no template code, no other site, no anchor-only link), without its query or fragment."""
    if not value or not value.startswith("/") or value.startswith("//") or "{" in value or "}" in value:
        return None
    return re.split(r"[?#]", value, 1)[0] or None


def templates(app):
    root = theme_dir(app) / "templates"
    return sorted(root.rglob("*.html"))


def test_the_theme_has_pages_to_check(app):
    assert len(templates(app)) >= 15


def test_every_form_of_the_theme_posts_to_a_route_that_accepts_that_method(app):
    offered = []
    for path in templates(app):
        for tag in re.findall(r"<form\b[^>]*>", path.read_text(encoding="utf-8"), re.I):
            action = own_address(attribute(tag, "action"))
            if action:
                offered.append((path.name, (attribute(tag, "method") or "get").upper(), action))
    assert offered, "no form with a literal address was found: the check itself is broken"
    refused = [(page, method, action) for page, method, action in offered if not answered(app, action, method)]
    assert not refused, f"forms that send a request nothing answers (page, method, address): {sorted(set(refused))}"


def test_every_link_of_the_theme_to_an_address_of_ours_leads_to_a_page_that_exists(app):
    links = set()
    for path in templates(app):
        for tag in re.findall(r"<a\b[^>]*>", path.read_text(encoding="utf-8"), re.I):
            address = own_address(attribute(tag, "href"))
            if address:
                links.add((path.name, address))
    assert links, "no link with a literal address was found: the check itself is broken"
    missing = sorted((page, address) for page, address in links if not answered(app, address, "GET"))
    assert not missing, f"links that lead nowhere (page, address): {missing}"


SCRIPT_ADDRESS = re.compile(r"""["'`](/(?:api/v1|admin|events|notifications|settings|teams|users|board|guide|scoreboard|challenges|stories|login|register|logout|confirm|reset_password|themes|plugins|ctftime|l3mon)[A-Za-z0-9_./\-]*)["'`]""")


def test_every_address_a_script_of_the_theme_calls_is_answered_by_some_route(app):
    called = set()
    for path in sorted((theme_dir(app) / "static").glob("*.js")):
        if path.name.endswith(".map"):
            continue
        for match in SCRIPT_ADDRESS.findall(path.read_text(encoding="utf-8", errors="replace")):
            address = match + "1" if match.endswith("/") else match  # "/api/v1/x/" + id: try it with an id
            called.add((path.name, address))
    assert called, "no address was found in the scripts: the check itself is broken"
    missing = sorted(
        (script, address) for script, address in called
        if not any(answered(app, address, method) for method in ("GET", "POST", "PUT", "PATCH", "DELETE"))
    )
    assert not missing, f"addresses a script calls that no route answers (script, address): {missing}"
