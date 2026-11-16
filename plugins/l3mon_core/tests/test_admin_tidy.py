"""The crew's Config page does not offer MajorLeagueCyber (assets/admin-tidy.css, registered in __init__.py).

CTFd's Config page has a MajorLeagueCyber tab (OAuth client ID and secret) and a "MajorLeagueCyber Only" choice for who may register. This event does
not use it. The preset settings pin the two OAuth values to empty (tools/l3mon/config.py), so nothing typed there can switch it on; this keeps the tab and
the choice out of sight, so that nobody wonders what they are for.

Run through tools/run-ctfd-tests.sh:
    tools/run-ctfd-tests.sh l3mon/ctfd:dev -- -q -p no:randomly -p no:cacheprovider /l3mon_tests/l3mon_core
"""
import re

from tests.helpers import create_ctfd, destroy_ctfd, login_as_user

SHEET = "/plugins/l3mon_core/assets/admin-tidy.css"


def test_the_config_page_loads_the_stylesheet_that_hides_majorleaguecyber():
    app = create_ctfd(enable_plugins=True)
    with app.app_context():
        admin = login_as_user(app, "admin")
        page = admin.get("/admin/config").get_data(as_text=True)
        assert 'href="#mlc"' in page, "the stock page still has the tab: the test is looking at the right page"
        assert SHEET in page, "the Config page does not load the stylesheet"
        with app.test_client() as visitor:
            sheet = visitor.get(SHEET)
        assert sheet.status_code == 200 and "text/css" in sheet.headers["Content-Type"]
        css = sheet.get_data(as_text=True)
        assert re.search(r'a\[href="#mlc"\]', css) and re.search(r"#mlc\b", css), "the tab and its panel are hidden"
        assert 'option[value="mlc"]' in css, 'the "MajorLeagueCyber Only" choice of the registration setting is hidden'
    destroy_ctfd(app)


def test_the_stylesheet_is_only_added_to_the_crews_pages_not_to_players():
    app = create_ctfd(enable_plugins=True)
    with app.app_context():
        with app.test_client() as visitor:
            assert SHEET not in visitor.get("/login").get_data(as_text=True)
    destroy_ctfd(app)
