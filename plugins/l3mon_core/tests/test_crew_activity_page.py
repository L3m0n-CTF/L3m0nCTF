"""The crew's activity page (/admin/l3mon/activity): filter the ledger, check the chain, export it, see the removals and lift one.

The page is a plain HTML shell plus one script that talks to the crew API (tests/test_crew_api.py). What matters about it:
  * only an administrator gets it;
  * the ledger holds text that PLAYERS control (a studio's name, a browser's identification), so the script builds the table with textContent and never with innerHTML;
  * a CSV export opens in a spreadsheet, where a cell that starts with = + - or @ is a formula: such cells are neutralised;
  * it is in the crew menu.

Run through tools/run-ctfd-tests.sh:
    tools/run-ctfd-tests.sh l3mon/ctfd:dev -- -q -p no:randomly -p no:cacheprovider /l3mon_tests/l3mon_core
"""
import os
import re

import pytest

from CTFd.models import db
from tests.helpers import create_ctfd, destroy_ctfd, gen_team, login_as_user

import CTFd.plugins.l3mon_core as _core

ASSETS = os.path.join(os.path.dirname(os.path.abspath(_core.__file__)), "assets")  # the tests are mounted elsewhere in the test container: find the script through the package


class P:
    def __init__(self, row):
        self.id, self.name = row.id, row.name


@pytest.fixture()
def world():
    app = create_ctfd(enable_plugins=True, user_mode="teams")
    with app.app_context():
        team = gen_team(db, name="Alpha", email="alpha@example.com", member_count=2)
        player = P(sorted(team.members, key=lambda u: u.id)[0])
        w = type("W", (), {})()
        w.app = app
        w.admin = login_as_user(app, "admin")
        w.player = login_as_user(app, player.name)
        yield w
    destroy_ctfd(app)


def script():
    return open(os.path.join(ASSETS, "activity.js"), encoding="utf-8").read()


def test_the_crew_gets_the_page_with_its_filters_and_buttons(world):
    r = world.admin.get("/admin/l3mon/activity")
    assert r.status_code == 200
    page = r.get_data(as_text=True)
    for wanted in ('id="activity-app"', 'data-api="/api/v1/l3mon/admin"', 'id="filter-team"', 'id="filter-actor"', 'id="filter-kind"', 'id="btn-verify"', 'id="btn-export"',
                   'id="removals-wrap"', "/plugins/l3mon_core/assets/activity.js"):
        assert wanted in page, wanted


def test_a_player_and_a_visitor_do_not_get_the_page(world):
    for client in (world.player, world.app.test_client()):
        r = client.get("/admin/l3mon/activity")
        assert r.status_code in (302, 403)
        assert b"activity-app" not in r.data


def test_the_script_is_served_and_the_page_is_in_the_crew_menu(world):
    assert world.admin.get("/plugins/l3mon_core/assets/activity.js").status_code == 200
    menu = world.admin.get("/admin/l3mon/activity").get_data(as_text=True)
    assert "/admin/l3mon/activity" in menu and "Activity log" in menu


def test_the_script_never_puts_ledger_text_into_the_page_as_markup():
    code = script()
    assert "innerHTML" not in code and "insertAdjacentHTML" not in code and "document.write" not in code and "outerHTML" not in code
    assert "textContent" in code and "createElement" in code


def test_the_csv_export_neutralises_cells_that_a_spreadsheet_would_run_as_formulas():
    code = script()
    assert re.search(r"\[=\+\\?-@\]", code) or "formula" in code.lower(), "the export guards against formulas"
    assert "'" in code and "csvCell" in code


def test_the_script_sends_the_csrf_token_when_it_lifts_a_removal_and_asks_before_doing_it():
    code = script()
    assert "CSRF-Token" in code and "confirm(" in code and "/removals/" in code and "/lift" in code


def test_the_script_calls_only_the_crew_api():
    code = script()
    urls = set(re.findall(r"/(?:api/v1/l3mon/admin|removals|activity)[A-Za-z0-9_/\-]*", code))
    assert urls, "it calls something"
    assert not re.search(r"/api/v1/(?!l3mon/admin)", code), "nothing outside the crew API"
