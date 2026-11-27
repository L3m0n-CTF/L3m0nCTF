"""The Discord blood posts (plugins/l3mon_board/blood.py): first, second and third blood, posted in the background.

The address comes only from the secret file; a test app never posts; a solve the freeze hides is never posted; the count is the real one
(not the freeze-filtered count a studio's reply uses), hidden studios neither post nor take a blood, the place and TRP are the public
scoreboard's, and names cannot carry Discord markdown or mentions.

Run through tools/run-ctfd-tests.sh:
    L3MON_MOUNT_PLUGINS=1 tools/run-ctfd-tests.sh l3mon/ctfd:dev -- -q -p no:randomly -p no:cacheprovider /l3mon_tests/l3mon_board/test_blood.py
"""
import calendar
import logging
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from CTFd.models import Teams, db
from CTFd.plugins.l3mon_board import blood
from CTFd.utils import set_config
from board_world import T_LIVE, clock, join_client, make_app, on_air, started, team_client, world
from tests.helpers import destroy_ctfd, login_as_user

ATTEMPT = "/api/v1/challenges/attempt"
HOOK = "https://discord.com/api/webhooks/123456789/the-token_part"


def send(client, challenge_id, flag):
    return client.post(ATTEMPT, json={"challenge_id": challenge_id, "submission": flag})


class _Now:
    """Runs the background post at once, so a test can read what was sent."""

    def __init__(self, target, args=(), daemon=None):
        self.target, self.args = target, args

    def start(self):
        self.target(*self.args)


@pytest.fixture()
def hook_file(tmp_path, monkeypatch):
    path = tmp_path / "DISCORD_BLOOD_WEBHOOK"
    path.write_text(HOOK + "\n", encoding="utf-8")
    monkeypatch.setenv("L3MON_DISCORD_BLOOD_WEBHOOK_FILE", str(path))
    return path


@pytest.fixture()
def posts():
    """What would have gone to Discord, as (url, payload). The test app's own silence is lifted here, and only here."""
    sent = []
    blood._NOTIFIED_SOLVES.clear()
    with patch.object(blood, "_send_webhook", side_effect=lambda url, payload: sent.append((url, payload))), \
         patch.object(blood.threading, "Thread", _Now), patch.object(blood, "_in_test_app", return_value=False):
        yield sent
    blood._NOTIFIED_SOLVES.clear()


@pytest.fixture()
def play():
    app = make_app()
    with app.app_context():
        started()
        with clock(T_LIVE):
            admin = login_as_user(app, "admin")
            ids = world(app, admin)
            on_air(admin, "lantern_walk", "moth_cipher", "noodle_ledger", "dumpling_gate", "coffee_break")
            alice = team_client(app, "alice", "studio-a")
            abe = join_client(app, "abe", "studio-a")
            bob = team_client(app, "bob", "studio-b")
            carol = team_client(app, "carol", "studio-c")
            dave = team_client(app, "dave", "studio-d")
            yield SimpleNamespace(app=app, admin=admin, ids=ids, alice=alice, abe=abe, bob=bob, carol=carol, dave=dave)
    destroy_ctfd(app)


def descriptions(sent):
    return [payload["embeds"][0]["description"] for _, payload in sent]


# ---- the address --------------------------------------------------------------------------------------------------------------

def test_there_is_no_address_in_the_code(monkeypatch, tmp_path):
    monkeypatch.setenv("L3MON_DISCORD_BLOOD_WEBHOOK_FILE", str(tmp_path / "absent"))
    assert blood.get_webhook_url() == ""
    assert not hasattr(blood, "DEFAULT_WEBHOOK")


@pytest.mark.parametrize("content", ["", "   \n", "http://discord.com/api/webhooks/1/x", "https://evil.example/api/webhooks/1/x",
                                     "https://discord.com.evil.example/api/webhooks/1/x", "https://discord.com/api/webhooks/1/x?wait=1"])
def test_an_empty_or_foreign_address_means_no_posts(monkeypatch, tmp_path, content):
    path = tmp_path / "hook"
    path.write_text(content, encoding="utf-8")
    monkeypatch.setenv("L3MON_DISCORD_BLOOD_WEBHOOK_FILE", str(path))
    assert blood.get_webhook_url() == ""


def test_the_address_is_read_from_the_file(hook_file):
    assert blood.get_webhook_url() == HOOK


def test_a_failed_post_never_writes_the_address_to_the_log(caplog):
    with caplog.at_level(logging.WARNING, logger="l3mon.blood"):
        blood._send_webhook("https://127.0.0.1:9/api/webhooks/1/the-secret-token", {"embeds": []})
    assert caplog.records and "the-secret-token" not in caplog.text


# ---- the words ----------------------------------------------------------------------------------------------------------------

def test_ordinal():
    for n, word in [(1, "1st"), (2, "2nd"), (3, "3rd"), (4, "4th"), (11, "11th"), (12, "12th"), (13, "13th"), (21, "21st"), (22, "22nd"),
                    (23, "23rd"), (101, "101st"), ("1st", "1st"), ("35th", "35th"), ("1stth", "1st"), (None, ""), ("?", ""), (0, "")]:
        assert blood.ordinal(n) == word


def test_an_unknown_place_is_left_out_rather_than_posted_as_first():
    """The place is unknown when the studio is not on the cached standings yet (stale cache, or only 0-TRP programmes): before, that read
    "in 1st place" (the review of 27 Nov)."""
    assert blood.create_blood_embed(0, "Hydra", "Echoes", None, 300)["description"] == "Hydra got first blood on Echoes and now has 300 TRP!"


def test_the_embeds_say_trp_and_carry_no_emoji():
    fb = blood.create_blood_embed(0, "Hydra", "Physics Trauma", 2, 19215)
    assert fb == {"title": "First Blood!", "description": "Hydra got first blood on Physics Trauma and is now in 2nd place with 19215 TRP!", "color": 16765503}
    assert blood.create_blood_embed(1, "Null Hunters", "Cicada", 35, 5985)["description"].endswith("in 35th place with 5985 TRP!")
    assert blood.create_blood_embed(2, "AutoBots", "Echoes", "1st", 2900)["title"] == "Third Blood!"
    assert blood.create_blood_embed(3, "T", "C", 5, 100) == {}
    for embed in (fb,):
        assert "points" not in embed["description"] and "\U0001fa78" not in embed["title"] + embed["description"]


def test_a_name_cannot_carry_markdown_a_link_or_a_second_line():
    text = blood.escape("[click](https://evil.example) **bold** @everyone\n# big")
    assert "\n" not in text
    assert "](" not in text.replace("\\]\\(", "")
    for raw in ("[", "]", "(", ")", "*", "#", "@", ":"):
        assert all(text[i - 1] == "\\" for i, ch in enumerate(text) if ch == raw), raw


# ---- through a real flag ------------------------------------------------------------------------------------------------------

def test_the_first_three_solves_post_first_second_and_third_blood_and_the_fourth_nothing(play, hook_file, posts):
    with patch.object(blood, "get_team_rank_and_score", wraps=blood.get_team_rank_and_score) as place:
        for client in (play.alice, play.bob, play.carol, play.dave):
            assert send(client, play.ids.lantern, "lantern-answer").get_json()["data"]["status"] == "correct"
    assert place.call_count == 3, "the fourth solve posts nothing, so it must not pay for a scoreboard rebuild in its reply (review of 27 Nov)"
    assert [p["embeds"][0]["title"] for _, p in posts] == ["First Blood!", "Second Blood!", "Third Blood!"]
    assert all(url == HOOK for url, _ in posts)
    assert descriptions(posts)[0].startswith("studio\\-a got first blood on Lantern Walk and is now in 1st place with ")
    assert all(p["allowed_mentions"] == {"parse": []} for _, p in posts), "no name may ping anybody"


def test_a_second_member_of_the_same_studio_does_not_post_again(play, hook_file, posts):
    send(play.alice, play.ids.lantern, "lantern-answer")
    send(play.abe, play.ids.lantern, "lantern-answer")  # already solved by the studio: CTFd records nothing
    assert len(posts) == 1


def test_a_test_app_never_posts_even_with_the_file(play, hook_file):
    blood._NOTIFIED_SOLVES.clear()
    with patch.object(blood, "_send_webhook") as sender, patch.object(blood.threading, "Thread", _Now):
        send(play.alice, play.ids.lantern, "lantern-answer")
    assert not sender.called


def test_without_the_file_nothing_is_posted(play, posts, monkeypatch, tmp_path):
    monkeypatch.setenv("L3MON_DISCORD_BLOOD_WEBHOOK_FILE", str(tmp_path / "absent"))
    send(play.alice, play.ids.lantern, "lantern-answer")
    assert posts == []


def test_a_hidden_studio_neither_posts_nor_takes_the_blood(play, hook_file, posts):
    ghost = team_client(play.app, "ghost", "studio-ghost")
    Teams.query.filter_by(name="studio-ghost").first().hidden = True
    db.session.commit()
    send(ghost, play.ids.coffee, "coffee-answer")
    assert posts == []
    send(play.alice, play.ids.coffee, "coffee-answer")
    assert [p["embeds"][0]["title"] for _, p in posts] == ["First Blood!"]


def test_the_place_and_trp_are_the_public_scoreboards(play, hook_file, posts):
    """A hidden studio (the crew's, a test studio) ahead on points does not push a real studio down: with the crew's view (admin=True)
    this post would say "2nd place"."""
    ghost = team_client(play.app, "ghost", "studio-ghost")
    Teams.query.filter_by(name="studio-ghost").first().hidden = True
    db.session.commit()
    for challenge, flag in ((play.ids.moth, "moth-answer"), (play.ids.noodle, "noodle-answer"), (play.ids.coffee, "coffee-answer")):
        send(ghost, challenge, flag)
    assert posts == []
    send(play.alice, play.ids.lantern, "lantern-answer")
    public = Teams.query.filter_by(name="studio-a").first().get_score(admin=False)
    assert public > 0
    assert descriptions(posts) == [f"studio\\-a got first blood on Lantern Walk and is now in 1st place with {public} TRP!"]


def test_nothing_is_posted_for_a_solve_after_the_freeze(play, hook_file, posts):
    """CTFd dates a solve with the real clock, so the freeze is set just before the real now: every solve here is after it. Before the
    fix every studio after the freeze was posted as "First Blood" with its live place (the review of 26 Nov)."""
    from freezegun.api import real_datetime

    set_config("freeze", calendar.timegm(real_datetime.utcnow().timetuple()) - 100)
    send(play.alice, play.ids.lantern, "lantern-answer")
    send(play.bob, play.ids.lantern, "lantern-answer")
    assert posts == []


def test_with_a_freeze_still_ahead_the_posts_go_on(play, hook_file, posts):
    """A freeze that is set but not reached yet stops nothing: solves before it are on the public board."""
    from freezegun.api import real_datetime

    set_config("freeze", calendar.timegm(real_datetime.utcnow().timetuple()) + 3600)
    send(play.alice, play.ids.lantern, "lantern-answer")
    send(play.bob, play.ids.lantern, "lantern-answer")
    assert [p["embeds"][0]["title"] for _, p in posts] == ["First Blood!", "Second Blood!"]


def test_a_studio_name_is_posted_as_plain_text(play, hook_file, posts):
    shady = team_client(play.app, "shady", "[win](https://evil.example)")
    send(shady, play.ids.moth, "moth-answer")
    assert descriptions(posts)[0].startswith("\\[win\\]\\(https\\:")
