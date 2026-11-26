"""The Beginners Section import (plugins/l3mon_release/beginner.py): 15 challenges become channel 0 ("Test Card"), all OFF AIR.

What is pinned: a dry run writes nothing; --apply makes 15 hidden challenges with a fixed value and a static case-sensitive flag, the channel `test-card` and 15 programmes (numbers
1 to 15, cells 0 to 14, delivery static_shared, the difficulty words of the platform), and NOTHING is on air, announced or visible; a second run changes nothing; a challenge that is
already there is never touched, and a different flag already stored is never replaced; one wrong entry refuses the whole file with every problem named and nothing written; no flag is
ever printed or put in an error; the task text holds the link to the lesson and never a flag.
The flags here are made at run time (a flag-shaped string in this file would trip the repository's scanner).
"""
import copy
import json

import pytest

from CTFd.models import Challenges, Flags, Notifications, db
from CTFd.plugins.l3mon_core.airing import on_air_ids
from CTFd.plugins.l3mon_core.models import Channel, Programme
from CTFd.plugins.l3mon_release import beginner
from tests.helpers import create_ctfd, destroy_ctfd

CATEGORIES = ["Web Security", "Cryptography", "Reverse Engineering", "Binary Exploitation", "Digital Forensics"]
DIFFICULTIES = ["Very Easy", "Easy", "Medium"]


def make_flag(i):
    return "L3m0n" + "{" + format(i + 1, "032x") + "}"


def sample(count=15):
    items = []
    for i in range(count):
        slug = f"episode-{i + 1:02d}"
        items.append({
            "no": i + 1, "category": CATEGORIES[i % 5], "challenge": f"Episode {i + 1}", "slug": slug, "difficulty": DIFFICULTIES[i % 3], "points": 50 + 25 * (i % 4),
            "flag": make_flag(i), "case_sensitive": True, "url": f"{beginner.BASE_URL}/c/{slug}", "description": f"The tagline of episode {i + 1}.",
        })
    return {"event": "L3m0nCTF 2026", "section": "Beginner", "challenges": items}


@pytest.fixture
def app():
    app = create_ctfd(enable_plugins=True)
    yield app
    destroy_ctfd(app)


def apply(raw, **kw):
    lines = []
    report = beginner.run(raw, apply=True, out=lines.append, **kw)
    return report, lines


def test_a_dry_run_writes_nothing_and_says_what_it_would_do(app):
    with app.app_context():
        lines = []
        report = beginner.run(sample(), apply=False, out=lines.append)
        assert report["applied"] is False and len(report["created"]) == 15
        assert Challenges.query.count() == 0 and Channel.query.count() == 0 and Programme.query.count() == 0
        assert any("dry run" in line for line in lines)


def test_apply_makes_the_challenges_the_channel_and_the_programmes_and_everything_stays_off_air(app):
    with app.app_context():
        report, lines = apply(sample())
        assert len(report["created"]) == 15 and report["not_withheld"] == 0
        rows = Challenges.query.order_by(Challenges.id).all()
        assert len(rows) == 15 and {r.state for r in rows} == {"hidden"} and {r.type for r in rows} == {"standard"}
        first = Challenges.query.filter_by(name="Episode 1").one()
        assert (first.category, first.value, first.max_attempts) == ("Web Security", 50, 0)
        flag = Flags.query.filter_by(challenge_id=first.id).one()
        assert flag.type == "static" and flag.content == make_flag(0) and not flag.data, "a static flag, case sensitive"
        channel = Channel.query.filter_by(slug="test-card").one()
        assert (channel.name, channel.position, channel.kind, channel.release_state) == ("Test Card", 0, "standard", "withheld")
        programmes = Programme.query.order_by(Programme.number).all()
        assert [p.number for p in programmes] == list(range(1, 16)) and [p.cell for p in programmes] == list(range(15))
        assert {p.delivery for p in programmes} == {"static_shared"} and {p.release_state for p in programmes} == {"withheld"}
        assert [p.difficulty for p in programmes[:4]] == ["warmup", "easy", "medium", "warmup"], "Very Easy is the platform's warmup"
        assert on_air_ids() == set(), "nothing is on air"
        assert Notifications.query.count() == 0, "nothing was announced"


def test_the_task_text_holds_the_link_to_the_lesson_and_never_a_flag(app):
    with app.app_context():
        apply(sample())
        for i in range(15):
            row = Challenges.query.filter_by(name=f"Episode {i + 1}").one()
            assert f'<a href="https://beginner.l3m0nctf.xyz/c/episode-{i + 1:02d}">' in row.description
            assert make_flag(i) not in row.description and f"The tagline of episode {i + 1}." in row.description
            assert "<script" not in row.description


def test_a_second_run_changes_nothing(app):
    with app.app_context():
        apply(sample())
        before = (Challenges.query.count(), Flags.query.count(), Programme.query.count(), Channel.query.count())
        report, _ = apply(sample())
        assert report["created"] == [] and len(report["kept"]) == 15
        assert (Challenges.query.count(), Flags.query.count(), Programme.query.count(), Channel.query.count()) == before


def test_a_challenge_that_is_there_is_never_touched_and_a_stored_flag_is_never_replaced(app):
    with app.app_context():
        apply(sample())
        row = Challenges.query.filter_by(name="Episode 3").one()
        row.value = 999
        row.description = "edited by the crew"
        Flags.query.filter_by(challenge_id=row.id).one().content = "kept-as-it-was"
        db.session.commit()
        report, lines = apply(sample())
        assert report["flag_differs"] == ["episode-03"]
        row = Challenges.query.filter_by(name="Episode 3").one()
        assert row.value == 999 and row.description == "edited by the crew"
        assert Flags.query.filter_by(challenge_id=row.id).one().content == "kept-as-it-was"
        assert not any("kept-as-it-was" in line or make_flag(2) in line for line in lines)


def mutate(change):
    data = copy.deepcopy(sample())
    change(data["challenges"])
    return data


@pytest.mark.parametrize("what, change, word", [
    ("a flag with the wrong shape", lambda c: c[4].update(flag="not-a-flag"), "entry 5 (episode-05): the flag does not have the shape"),
    ("a flag used twice", lambda c: c[7].update(flag=c[2]["flag"]), "entry 8 (episode-08): the same flag"),
    ("a slug used twice", lambda c: c[9].update(slug=c[1]["slug"], url=c[1]["url"]), "the slug is used twice"),
    ("a slug with capitals", lambda c: c[0].update(slug="Episode-01"), "the slug must be lowercase"),
    ("no points", lambda c: c[3].update(points=0), "points must be a whole number"),
    ("points as text", lambda c: c[3].update(points="50"), "points must be a whole number"),
    ("an unknown difficulty", lambda c: c[2].update(difficulty="Brutal"), "is not one of"),
    ("an address on another site", lambda c: c[1].update(url="https://example.com/c/episode-02"), "the address must be"),
    ("markup in the tagline", lambda c: c[6].update(description="<script>x</script>"), "plain text"),
    ("only 14 challenges", lambda c: c.pop(), "expected 15 challenges"),
])
def test_one_wrong_entry_refuses_the_whole_file_with_the_problem_named_and_nothing_written(app, what, change, word):
    with app.app_context():
        with pytest.raises(beginner.Refused) as refused:
            beginner.run(mutate(change), apply=True, out=lambda line: None)
        assert word in "; ".join(refused.value.problems), (what, refused.value.problems)
        assert Challenges.query.count() == 0 and Programme.query.count() == 0 and Channel.query.count() == 0
        assert not any(make_flag(i) in str(refused.value) for i in range(15)), "an error never repeats a flag"


def test_text_that_is_not_json_and_a_wrong_shape_are_refused(app):
    with app.app_context():
        for raw in ("this is not json", "[]", json.dumps({"challenges": "nope"}), json.dumps({"other": 1})):
            with pytest.raises(beginner.Refused):
                beginner.run(raw, apply=True, out=lambda line: None)
        assert Challenges.query.count() == 0


def test_no_flag_is_ever_printed(app):
    with app.app_context():
        _, lines = apply(sample())
        beginner.run(sample(), apply=False, out=lines.append)
        text = "\n".join(lines)
        assert not any(make_flag(i) in text for i in range(15)) and "L3m0n" + "{" not in text.replace("L3m0n{...}", "")


def test_the_json_text_form_is_read_like_the_parsed_form(app):
    with app.app_context():
        report, _ = apply(json.dumps(sample()))
        assert len(report["created"]) == 15
