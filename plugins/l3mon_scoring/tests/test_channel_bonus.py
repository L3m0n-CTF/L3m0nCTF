"""The channel bonus: a studio that solves EVERY programme of a channel is given a fixed number of TRP, once per channel (owner, 23 Nov 2026).

Before this there was nothing: a correct flag's reply said whether the channel was complete (`channel_complete`) and nothing more, and a bonus existed only when the
crew gave one by hand. Now the platform gives it itself. The rules, each pinned below:

- the amount is one fixed number for every channel (default 100 TRP, set by the crew, 0 switches it off);
- it is given once per studio and channel, whoever of the studio's members makes the last solve, and a database key (team, channel) makes a second one
  impossible even when two members finish a channel at the same moment;
- it counts everywhere TRP counts (it is a plain bonus award on the captain), and the studio is told in its private lines;
- it is given when the last solve is recorded: by a player's flag, by the crew's "mark correct", by a Restore, and at the latest by the minute check;
- it is never taken back because the crew ADDS a programme to the channel later (the studio earned it), but it is taken back when a Revoke sets aside a solve of
  that channel and the channel is no longer complete, and given again by the Restore.

Run through tools/run-ctfd-tests.sh:
    L3MON_MOUNT_PLUGINS=1 tools/run-ctfd-tests.sh l3mon/ctfd:dev -- -q -p no:randomly -p no:cacheprovider /l3mon_tests/l3mon_scoring
"""
import pytest

from CTFd.models import Awards, Solves, Submissions, Teams, db
from CTFd.plugins.l3mon_core.models import Audit, Bonus, Channel, ChannelBonus, Note, Programme
from CTFd.plugins.l3mon_scoring import channel_bonus, voids
from CTFd.plugins.l3mon_scoring.errors import Refused
from CTFd.utils import set_config
from scoring_world import attempt, fixed, make_app, score_of, solve, standings, studio, team_client, team_of
from tests.helpers import destroy_ctfd, login_as_user


@pytest.fixture()
def app():
    app = make_app()
    with app.app_context():
        yield app
    destroy_ctfd(app)


_number = [100]


def make_channel(slug, name=None, position=1, kind="standard", programmes=3, value=100, flags=False):
    """A channel with `programmes` programmes, all on air. Returns (channel, [challenge, ...])."""
    row = Channel(slug=slug, name=name or slug.title(), position=position, kind=kind, sponsor_name="Acme" if kind == "sponsored" else None, release_state="released")
    db.session.add(row)
    db.session.commit()
    challenges = []
    for cell in range(programmes):
        challenge = fixed(f"{slug}-{cell}", value=value, flag=f"{slug}-{cell}-answer" if flags else None)
        add_programme(row, challenge, cell)
        challenges.append(challenge)
    return row, challenges


def add_programme(channel, challenge, cell):
    _number[0] += 1
    db.session.add(Programme(challenge_id=challenge.id, channel_id=channel.id, cell=cell, number=_number[0], slug=f"{channel.slug}-p{cell}-{_number[0]}", release_state="released"))
    db.session.commit()


def solve_all(team, challenges, **kw):
    for minute, challenge in enumerate(challenges):
        solve(team, challenge, minutes=minute, **kw)


def awards_of(team_id):
    db.session.rollback()
    return Awards.query.filter_by(team_id=team_id).all()


# ---- the amount ----------------------------------------------------------------------------------------------------------------

def test_the_default_amount_is_100_and_the_crew_can_change_or_switch_it_off(app):
    assert channel_bonus.amount() == channel_bonus.DEFAULT_TRP == 100
    got = channel_bonus.set_amount(250)
    assert got == {"trp": 250, "was": 100} and channel_bonus.amount() == 250
    assert channel_bonus.set_amount(0)["trp"] == 0 and channel_bonus.amount() == 0
    assert Audit.query.filter_by(action="scoring.channel_bonus").count() == 2, "every change of the amount is in the audit trail"


@pytest.mark.parametrize("bad", [-1, 1001, True, False, 1.5, "100", None, [100]])
def test_an_amount_that_makes_no_sense_is_refused_and_changes_nothing(app, bad):
    with pytest.raises(Refused):
        channel_bonus.set_amount(bad)
    assert channel_bonus.amount() == 100 and Audit.query.filter_by(action="scoring.channel_bonus").count() == 0


def test_a_stored_amount_that_is_garbage_reads_as_the_default_never_as_a_free_bonus(app):
    set_config(channel_bonus.KEY, "banana")
    assert channel_bonus.amount() == 100
    set_config(channel_bonus.KEY, "-40")
    assert channel_bonus.amount() == 100
    set_config(channel_bonus.KEY, "999999")
    assert channel_bonus.amount() == 100


# ---- giving it -----------------------------------------------------------------------------------------------------------------

def test_solving_every_programme_of_a_channel_gives_the_studio_the_bonus_once(app):
    team = studio("alpha", members=3)
    _, challenges = make_channel("street")
    solve_all(team, challenges)
    before = score_of(team.id)
    given = channel_bonus.award(team.id)
    assert [(g["trp"]) for g in given] == [100]
    assert score_of(team.id) == before + 100 == 400
    assert standings() == [("alpha", 400)]
    award = Awards.query.one()
    assert (award.user_id, award.team_id, award.value, award.category, award.name) == (team.captain_id, team.id, 100, "bonus", "Channel bonus +100 TRP")
    assert not award.description, "the award's text is empty like every bonus: nothing about a programme can leak through it"
    row = ChannelBonus.query.one()
    assert (row.team_id, row.state, row.trp, row.award_id) == (team.id, "awarded", 100, award.id)
    assert Bonus.query.one().scope == "team" and Bonus.query.one().award_id == award.id
    line = Note.query.one()
    assert line.team_id == team.id and "Channel bonus +100 TRP" in line.title and "Street" in line.text


def test_nothing_is_given_before_the_last_programme_is_solved(app):
    team = studio("alpha")
    _, challenges = make_channel("street", programmes=3)
    solve_all(team, challenges[:2])
    assert channel_bonus.award(team.id) == [] and Awards.query.count() == 0 and ChannelBonus.query.count() == 0


def test_a_programme_that_is_not_on_air_yet_still_has_to_be_solved(app):
    """A channel is complete when every programme the plan puts in it is solved, released or not: the reply's own `channel_complete` says the same."""
    team = studio("alpha")
    channel, challenges = make_channel("street", programmes=2)
    later = fixed("street-later", value=100)
    add_programme(channel, later, 2)
    Programme.query.filter_by(challenge_id=later.id).one().release_state = "withheld"
    db.session.commit()
    solve_all(team, challenges)
    assert channel_bonus.award(team.id) == []
    solve(team, later, minutes=9)
    assert len(channel_bonus.award(team.id)) == 1


def test_each_channel_gives_its_own_bonus_and_a_sponsored_channel_counts_like_any_other(app):
    team = studio("alpha")
    _, street = make_channel("street", position=1, programmes=2)
    _, snack = make_channel("snack", position=2, programmes=2)
    _, brk = make_channel("break", position=7, kind="sponsored", programmes=1)
    solve_all(team, street + snack + brk)
    given = channel_bonus.award(team.id)
    assert len(given) == 3 and ChannelBonus.query.count() == 3 and Awards.query.count() == 3
    assert score_of(team.id) == 5 * 100 + 3 * 100


def test_it_is_given_once_however_often_it_is_asked(app):
    team = studio("alpha")
    _, challenges = make_channel("street")
    solve_all(team, challenges)
    assert len(channel_bonus.award(team.id)) == 1
    for _ in range(3):
        assert channel_bonus.award(team.id) == []
    assert Awards.query.count() == 1 and ChannelBonus.query.count() == 1 and Note.query.count() == 1


def test_a_second_bonus_that_races_the_first_is_refused_by_the_database_and_leaves_no_orphan_award(app, monkeypatch):
    """Two members finish a channel at the same moment: both workers see 'not given yet'. The (team, channel) key lets one win; the loser's award row, made in
    the same savepoint, goes with it, so the studio is never paid twice and no award is left without its record."""
    team = studio("alpha")
    channel, challenges = make_channel("street")
    solve_all(team, challenges)
    db.session.add(ChannelBonus(team_id=team.id, channel_id=channel.id, state="awarded", trp=100))  # the winner's record, written between our look and our write
    db.session.commit()
    monkeypatch.setattr(channel_bonus, "_existing", lambda team_id: {})  # our look happened BEFORE the winner wrote: it saw no record
    assert channel_bonus.award(team.id) == []
    assert Awards.query.count() == 0, "no award may be left behind by the loser"
    assert ChannelBonus.query.count() == 1


def test_whichever_member_makes_the_last_solve_the_studio_gets_one_bonus_on_the_captain(app):
    team = studio("alpha", members=3)
    _, challenges = make_channel("street", programmes=3)
    for i, challenge in enumerate(challenges):
        solve(team, challenge, minutes=i, member=i)  # a different member each time
    channel_bonus.award(team.id)
    assert Awards.query.one().user_id == team.captain_id


def test_a_studio_without_a_captain_gives_the_bonus_to_its_first_member(app):
    team = studio("alpha", members=2)
    first = sorted(team.members, key=lambda u: u.id)[0]
    _, challenges = make_channel("street", programmes=1)
    solve_all(team, challenges)
    team.captain_id = None
    db.session.commit()
    channel_bonus.award(team.id)
    assert Awards.query.one().user_id == first.id


def test_a_channel_with_no_programme_never_gives_a_bonus(app):
    team = studio("alpha")
    db.session.add(Channel(slug="empty", name="Empty", position=3, release_state="released"))
    db.session.commit()
    assert channel_bonus.award(team.id) == []


def test_amount_zero_switches_it_off_and_raising_it_later_pays_the_channels_already_completed(app):
    team = studio("alpha")
    _, challenges = make_channel("street")
    solve_all(team, challenges)
    channel_bonus.set_amount(0)
    assert channel_bonus.award(team.id) == [] and Awards.query.count() == 0
    channel_bonus.set_amount(40)
    assert channel_bonus.sweep() == 1
    assert Awards.query.one().value == 40 and ChannelBonus.query.one().trp == 40


def test_a_changed_amount_does_not_change_what_was_already_given(app):
    team = studio("alpha")
    _, street = make_channel("street", programmes=1)
    _, snack = make_channel("snack", position=2, programmes=1)
    solve_all(team, street)
    channel_bonus.award(team.id)
    channel_bonus.set_amount(500)
    solve_all(team, snack)
    channel_bonus.award(team.id)
    assert sorted(a.value for a in Awards.query.all()) == [100, 500]


def test_a_programme_added_to_a_channel_later_does_not_take_the_bonus_back(app):
    team = studio("alpha")
    channel, challenges = make_channel("street", programmes=2)
    solve_all(team, challenges)
    channel_bonus.award(team.id)
    add_programme(channel, fixed("street-new", value=100), 2)
    assert channel_bonus.award(team.id) == [] and channel_bonus.revoke_broken(team.id, []) == []
    assert Awards.query.count() == 1 and ChannelBonus.query.one().state == "awarded"


def test_the_minute_sweep_gives_what_a_missed_hook_did_not(app):
    """Solves written straight into the database (a script, an import) have no request to notice them: the minute check gives the bonus."""
    team_a, team_b = studio("alpha"), studio("beta")
    _, challenges = make_channel("street")
    solve_all(team_a, challenges)
    solve_all(team_b, challenges[:2])
    assert channel_bonus.sweep() == 1
    assert [a.team_id for a in Awards.query.all()] == [team_a.id]
    assert channel_bonus.sweep() == 0


# ---- a solve that is set aside ---------------------------------------------------------------------------------------------------

def test_a_revoke_of_a_programme_of_that_channel_takes_the_bonus_back_and_a_restore_gives_it_again(app):
    team = studio("alpha")
    _, challenges = make_channel("street", programmes=3)
    solve_all(team, challenges)
    channel_bonus.award(team.id)
    assert score_of(team.id) == 3 * 100 + 100
    voids.revoke(challenges[1].id, "the flag was broken")
    assert score_of(team.id) == 2 * 100, "the solve's TRP and the channel bonus both come off"
    assert Awards.query.count() == 0
    row = ChannelBonus.query.one()
    assert row.state == "revoked" and row.award_id is None
    assert any("withdrawn" in n.title.lower() for n in Note.query.filter_by(team_id=team.id).all())
    voids.restore(challenges[1].id, "fixed")
    assert score_of(team.id) == 3 * 100 + 100
    row = ChannelBonus.query.one()
    assert row.state == "awarded" and row.award_id == Awards.query.one().id
    assert ChannelBonus.query.count() == 1, "the same record is used again: the (team, channel) key still allows only one"


def test_a_revoke_of_a_programme_of_another_channel_leaves_the_bonus_alone(app):
    team = studio("alpha")
    _, street = make_channel("street", programmes=2)
    _, snack = make_channel("snack", position=2, programmes=2)
    solve_all(team, street + snack)
    channel_bonus.award(team.id)
    voids.revoke(snack[0].id, "broken")
    states = {(r.channel_id): r.state for r in ChannelBonus.query.all()}
    assert sorted(states.values()) == ["awarded", "revoked"]
    assert score_of(team.id) == 3 * 100 + 100, "the street bonus stays; the snack channel lost its bonus and one solve"


def test_a_revoke_when_no_bonus_was_given_changes_nothing_about_bonuses(app):
    team = studio("alpha")
    _, challenges = make_channel("street", programmes=3)
    solve_all(team, challenges[:2])
    voids.revoke(challenges[0].id, "broken")
    assert ChannelBonus.query.count() == 0 and Awards.query.count() == 0


# ---- through the real routes ---------------------------------------------------------------------------------------------------------------

def test_the_last_flag_of_a_channel_gives_the_bonus_by_the_end_of_that_request(app):
    _, challenges = make_channel("street", programmes=2, flags=True)
    first, second = challenges[0].id, challenges[1].id  # a request ends the session: the rows are read before it
    alice = team_client(app, "alice", "studio-a")
    assert attempt(alice, first, "street-0-answer") == "correct"
    team_id = team_of("alice").id
    assert ChannelBonus.query.count() == 0
    assert attempt(alice, second, "street-1-answer") == "correct"
    db.session.rollback()
    assert ChannelBonus.query.filter_by(team_id=team_id).count() == 1
    assert score_of(team_id) == 2 * 100 + 100


def test_a_solve_the_crew_adds_by_hand_gives_the_bonus_too(app):
    _, challenges = make_channel("street", programmes=1, flags=True)
    only = challenges[0].id
    team = studio("alpha")
    team_id, user_id = team.id, sorted(team.members, key=lambda u: u.id)[0].id
    admin = login_as_user(app, "admin")
    r = admin.post("/api/v1/submissions", json={"user_id": user_id, "team_id": team_id, "challenge_id": only, "provided": "x", "type": "correct"})
    assert r.status_code == 200, r.get_data(as_text=True)
    db.session.rollback()
    assert ChannelBonus.query.filter_by(team_id=team_id).count() == 1


def test_the_reply_to_the_last_flag_names_the_bonus_and_an_earlier_flag_names_none(app):
    _, challenges = make_channel("street", programmes=2, flags=True)
    one, two = challenges[0].id, challenges[1].id
    alice = team_client(app, "alice", "studio-a")
    first = alice.post("/api/v1/challenges/attempt", json={"challenge_id": one, "submission": "street-0-answer"}).get_json()["data"]
    assert first["l3mon"]["channel_complete"] is False and first["l3mon"]["channel_bonus"] == 0
    last = alice.post("/api/v1/challenges/attempt", json={"challenge_id": two, "submission": "street-1-answer"}).get_json()["data"]
    assert last["l3mon"]["channel_complete"] is True and last["l3mon"]["channel_bonus"] == 100


def test_a_wrong_flag_never_gives_anything(app):
    _, challenges = make_channel("street", programmes=1, flags=True)
    only = challenges[0].id
    alice = team_client(app, "alice", "studio-a")
    assert attempt(alice, only, "nope") == "incorrect"
    db.session.rollback()
    assert ChannelBonus.query.count() == 0 and Awards.query.count() == 0


# ---- the crew's door ---------------------------------------------------------------------------------------------------------------

def test_the_crew_reads_and_sets_the_amount_through_the_api_and_a_player_cannot(app):
    admin = login_as_user(app, "admin")
    r = admin.get("/api/v1/l3mon/admin/scoring")
    assert r.status_code == 200 and r.get_json()["data"]["channel_bonus"] == {"trp": 100, "default": 100, "max": 1000}
    r = admin.put("/api/v1/l3mon/admin/scoring/channel-bonus", json={"trp": 150})
    assert r.status_code == 200 and r.get_json()["data"]["trp"] == 150 and channel_bonus.amount() == 150
    r = admin.put("/api/v1/l3mon/admin/scoring/channel-bonus", json={"trp": 5000})
    assert r.status_code == 400 and channel_bonus.amount() == 150
    r = admin.put("/api/v1/l3mon/admin/scoring/channel-bonus", json={})
    assert r.status_code == 400
    player = team_client(app, "alice", "studio-a")
    assert player.put("/api/v1/l3mon/admin/scoring/channel-bonus", json={"trp": 1}).status_code in (302, 401, 403)
    assert channel_bonus.amount() == 150
