"""Two things about the generated theme folder (ui/theme/l3mon) that a plain "regenerate the theme" must never undo.

1. Two files hold text the UI source cannot produce yet (found by the audit of 19 November 2026, when the exporter was run on the unmerged UI branch and its
   output was compared with what is deployed): the Guide's list of the programmes each member solved was added BY HAND to `_guide.html` (the exporter's
   template language has no nested loop), and the public studio page `_team_public.html` has no UI page at all. Regenerating `_guide.html` would silently
   remove the first. These tests fail the moment either is lost, until the exporter learns to make them.
2. The registration steps no longer carry a "Back" button (19 November 2026): on step 2 it pointed at `/register/account`, which sends a signed-in person to
   the board and the board sends an unverified person straight back to `/confirm`, so it went nowhere; step 3 had the same dead end.

Run through tools/run-ctfd-tests.sh with the working-tree theme and plugins mounted (see docs/deploy/theme-slices.md).
"""
from pathlib import Path

import pytest

from tests.helpers import create_ctfd, destroy_ctfd


@pytest.fixture
def app():
    app = create_ctfd(enable_plugins=True, ctf_theme="l3mon", user_mode="teams", ctf_name="L3m0n test event")
    yield app
    destroy_ctfd(app)


def templates(app) -> Path:
    return Path(app.root_path) / "themes" / "l3mon" / "templates"


def test_the_guide_template_still_draws_each_members_solved_programmes(app):
    text = (templates(app) / "_guide.html").read_text(encoding="utf-8")
    assert "gd-solves-list" in text and "item.solve_items" in text, (
        "the per-member solves in _guide.html were lost: the file was regenerated from a UI source that does not have them (see this file's docstring)"
    )


def test_the_public_studio_template_is_still_there_and_escapes_what_it_prints(app):
    text = (templates(app) / "_team_public.html").read_text(encoding="utf-8")
    assert "{{" in text
    assert "|safe" not in text and "| safe" not in text, "a studio's name, members and solves are player-chosen text: nothing on this page may be marked safe"
    assert (templates(app) / "teams" / "public.html").is_file()


@pytest.mark.parametrize("name", ["_register-verify.html", "_register-studio.html"])
def test_the_registration_steps_have_no_back_button(app, name):
    text = (templates(app) / name).read_text(encoding="utf-8")
    assert "data-back" not in text, f"{name} still has a Back button"
    assert "/register/account" not in text, f"{name} still links to the step that no longer exists for a signed-in person"


def test_the_studio_step_keeps_its_one_button_on_the_right(app):
    """With the Back button gone the actions container must not be the "split" one (it would push the lone button to the left)."""
    text = (templates(app) / "_register-studio.html").read_text(encoding="utf-8")
    assert 'class="reg-actions"' in text and "reg-actions-split" not in text
    assert 'data-submit' in text


def test_the_script_no_longer_wires_a_back_button(app):
    static = Path(app.root_path) / "themes" / "l3mon" / "static"
    bundle = next(static.glob("register.*.js"))
    assert "data-back" not in bundle.read_text(encoding="utf-8")
