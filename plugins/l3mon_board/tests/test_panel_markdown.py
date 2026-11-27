"""The task text and the hints are Markdown (27 Nov 2026): the panel gets them as HTML made by CTFd's own Markdown converter, sanitised.

CTFd keeps a challenge's description and a hint's content as the Markdown the crew typed in the admin editor (bold, italics, headings, quotes,
lists, links, code, tables). Its own theme converts them when it shows them; the panel used to take the Markdown for HTML, so `**Flag Format**`
reached players with its asterisks and every line break was lost. Now:

- `l3mon.description_html` is the description converted (CTFd's `build_markdown`, with its sanitiser on whatever the platform's setting), and
  CTFd's own `description` key is kept unchanged (nothing taken away);
- each unlocked hint in CTFd's answer gains `html` beside CTFd's `content`; a locked hint still carries neither;
- the page's own allowlist (safe-html.js) is the second line: these tests pin the first.

Run through tools/run-ctfd-tests.sh:
    L3MON_MOUNT_PLUGINS=1 tools/run-ctfd-tests.sh l3mon/ctfd:dev -- -q -p no:randomly -p no:cacheprovider /l3mon_tests/l3mon_board/test_panel_markdown.py
"""
from types import SimpleNamespace

import pytest

from CTFd.models import Challenges, Hints, db
from board_world import T_LIVE, clock, join_client, make_app, on_air, started, team_client, world
from tests.helpers import destroy_ctfd, login_as_user

LIST = "/api/v1/challenges"

TASK = (
    "The rack is **licensed** per *chassis*.\n"
    "\n"
    "## Calibration\n"
    "\n"
    "- one\n"
    "- two\n"
    "\n"
    "1. first\n"
    "2. second\n"
    "\n"
    "> a quoted line\n"
    "\n"
    "See [the manual](https://example.org/manual) and `make check`, not ~~the old way~~.\n"
    "\n"
    "| part | count |\n"
    "|------|-------|\n"
    "| rack | 1 |\n"
    "\n"
    "**Flag Format**: the usual one.\n"
)


def detail(client, challenge_id):
    r = client.get(f"{LIST}/{challenge_id}")
    assert r.status_code == 200, r.get_data(as_text=True)
    return r.get_json()["data"]


@pytest.fixture()
def play():
    app = make_app()
    with app.app_context():
        started()
        with clock(T_LIVE):
            admin = login_as_user(app, "admin")
            ids = world(app, admin)
            on_air(admin, "lantern_walk", "moth_cipher")
            alice = team_client(app, "alice", "studio-a")
            abe = join_client(app, "abe", "studio-a")
            yield SimpleNamespace(app=app, admin=admin, ids=ids, alice=alice, abe=abe)
    destroy_ctfd(app)


def describe(challenge_id, text):
    challenge = Challenges.query.get(challenge_id)
    challenge.description = text
    db.session.commit()


def test_the_task_text_reaches_the_panel_converted_from_markdown(play):
    describe(play.ids.lantern, TASK)
    data = detail(play.alice, play.ids.lantern)
    html = data["l3mon"]["description_html"]
    for piece in ("<strong>licensed</strong>", "<em>chassis</em>", "<h2>Calibration</h2>", "<li>one</li>", "<ol>", "<blockquote>",
                  '<a href="https://example.org/manual"', "<code>make check</code>", "<del>the old way</del>", "<table>", "<td>rack</td>",
                  "<strong>Flag Format</strong>"):
        assert piece in html, piece
    assert "**" not in html and "~~" not in html and "## " not in html, "no Markdown left over"
    assert html.count("<p>") >= 4, "the paragraphs stay paragraphs"
    assert data["description"] == TASK, "CTFd's own key is kept as it is (one key added, none taken away)"


def test_markup_the_crew_could_paste_is_sanitised_on_the_server_too(play):
    # each on its own paragraph: a line that starts with raw HTML is an HTML block to Markdown, and the rest of that block stays literal text
    describe(play.ids.lantern, "\n\n".join([
        "<script>alert(1)</script>", '<img src=x onerror="alert(2)">', "[x](javascript:alert(3))", '<iframe src="https://e.x"></iframe>',
        '<form action="https://e.x"><input name="p"></form>', '<p style="color:red" onclick="alert(4)">styled</p>', "**ok**",
    ]))
    html = detail(play.alice, play.ids.lantern)["l3mon"]["description_html"]
    for bad in ("<script", "alert(1)", "onerror", 'href="javascript', "<iframe", "<form", "<input", "style=", "onclick"):
        assert bad not in html, bad
    assert "<strong>ok</strong>" in html and "styled" in html


def test_links_and_pictures_keep_only_safe_addresses(play):
    describe(play.ids.lantern, "[docs](https://example.org/d) [file](/files/ab/notes.txt) ![chart](/files/cd/chart.png) [mail](mailto:crew@example.org)")
    html = detail(play.alice, play.ids.lantern)["l3mon"]["description_html"]
    assert '<a href="https://example.org/d" rel="noopener noreferrer nofollow">docs</a>' in html
    assert 'href="/files/ab/notes.txt"' in html and '<img src="/files/cd/chart.png" alt="chart">' in html


def test_an_empty_or_missing_description_is_an_empty_string(play):
    describe(play.ids.lantern, "")
    assert detail(play.alice, play.ids.lantern)["l3mon"]["description_html"] == ""


def test_an_unlocked_hint_carries_its_text_as_html_and_a_locked_one_carries_none(play):
    hint = Hints.query.filter_by(challenge_id=play.ids.lantern).order_by(Hints.id).first()
    hint.content = "Look **left**, then:\n\n- up\n- down"
    db.session.commit()
    hint = SimpleNamespace(id=hint.id)  # the requests below end the session the row belongs to
    before = detail(play.alice, play.ids.lantern)["hints"]
    assert all("content" not in h and "html" not in h for h in before), "a locked hint gives nothing away"
    solved = play.alice.post(f"{LIST}/attempt", json={"challenge_id": play.ids.lantern, "submission": "lantern-answer"})  # TRP to pay with
    assert solved.get_json()["data"]["status"] == "correct"
    assert play.alice.post("/api/v1/unlocks", json={"target": hint.id, "type": "hints"}).status_code == 200
    hints = detail(play.abe, play.ids.lantern)["hints"]
    opened = next(h for h in hints if h["id"] == hint.id)
    assert opened["content"] == "Look **left**, then:\n\n- up\n- down", "CTFd's own key is kept"
    assert "<strong>left</strong>" in opened["html"] and "<li>up</li>" in opened["html"] and "**" not in opened["html"]
    assert all("html" not in h for h in hints if h["id"] != hint.id)
