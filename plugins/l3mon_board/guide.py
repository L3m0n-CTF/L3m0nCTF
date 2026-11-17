"""The Guide (SP3 part 3.5): `GET /api/v1/l3mon/guide`, the studio's own numbers, what each member brought in, the progress of every channel,
the story meter, and `epg_sig` (what the programme grid shows this studio; the grid itself is `epg.py`).

Counting is the board's: a solve is the studio's, a member's and a channel's only while its programme is on the plan **and** the viewer may
see it, so a programme the crew pulled back leaves every count here although its TRP stays in the score. Hint costs are paid by the whole
studio and are in nobody's line; a bonus belongs to the whole studio and is in nobody's line either. A solve nobody can be named for (the
member left or was suspended) is one line, "Earlier solves", id 0. Only a studio's own members are ever listed, and only to that studio.

The crew belongs to no studio: `team` is null for them. Nothing here is a date or a countdown.
"""
import datetime
import math

from CTFd.models import Awards, Solves, Unlocks, Users, db
from CTFd.plugins.l3mon_board import banner, board, counts, envelope, epg, figures, hooks, plan
from CTFd.plugins.l3mon_core.clock import current_phase
from CTFd.plugins.l3mon_core.models import LIVE_DELIVERIES, Note
from CTFd.plugins.l3mon_core.settings import story_air_target, story_meter
from CTFd.plugins.l3mon_core.tick import signature

NOTES = 5  # the studio's newest private lines that are sent
EARLIER = "Earlier solves"


def _members(team, viewer, credited):
    """Each member of the studio with what they brought in. `credited` is [(user id, value, challenge name, timestamp)]."""
    people = {}
    for user_id, name in db.session.query(Users.id, Users.name).filter(Users.team_id == team.id, Users.banned.is_(False)).all():
        people[user_id] = {
            "id": user_id,
            "name": name,
            "captain": team.captain_id == user_id,
            "you": viewer.user is not None and viewer.user.id == user_id,
            "solves": 0,
            "trp": 0,
            "solve_items": [],
        }
    earlier = {"id": 0, "name": EARLIER, "captain": False, "you": False, "solves": 0, "trp": 0, "solve_items": []}
    for item in credited:
        user_id = item[0]
        value = item[1]
        name = item[2] if len(item) > 2 else ""
        time_str = item[3] if len(item) > 3 else ""
        line = people.get(user_id, earlier)
        line["solves"] += 1
        line["trp"] += value
        if name:
            line["solve_items"].append({"name": name, "trp": value, "time": time_str})
    lines = list(people.values()) + ([earlier] if earlier["solves"] else [])
    gross = sum(line["trp"] for line in lines)
    for line in lines:
        line["pct"] = figures.js_round(line["trp"] / gross * 100) if gross > 0 else 0
    lines.sort(key=lambda line: (-line["trp"], not line["captain"], line["name"].casefold(), line["name"]))
    return [{key: line[key] for key in ("id", "name", "captain", "you", "solves", "trp", "pct", "solve_items")} for line in lines]


def _team_block(viewer, phase, view):
    team = viewer.team
    score, place, of = board.numbers(team, phase)
    mine = [(programme, challenge) for programme, challenge in view.rows if challenge.id in view.shown and challenge.id in viewer.solved]
    value_of = {challenge.id: int(challenge.value or 0) for _, challenge in mine}
    name_of = {challenge.id: str(challenge.name or "") for _, challenge in mine}
    solves_rows = (
        db.session.query(Solves.user_id, Solves.challenge_id, Solves.date)
        .filter(Solves.account_id == team.id)
        .order_by(Solves.date.desc())
        .all()
    )
    credited = []
    for user_id, challenge_id, solve_date in solves_rows:
        if challenge_id in value_of:
            ist_time = solve_date + datetime.timedelta(hours=5, minutes=30) if solve_date else None
            time_str = ist_time.strftime("%d %b, %H:%M IST") if ist_time else ""
            credited.append((user_id, value_of[challenge_id], name_of.get(challenge_id, "Programme"), time_str))
    live = 0
    for programme, challenge in view.rows:
        if challenge.id in view.shown and programme.delivery in LIVE_DELIVERIES:
            state = hooks.instance_for(team, challenge.id)
            if state is not None and state.get("state") == "running":
                live += 1
    bonus = db.session.query(db.func.coalesce(db.func.sum(Awards.value), 0)).filter(Awards.team_id == team.id, Awards.category == "bonus").scalar()
    notes = Note.query.filter_by(team_id=team.id).order_by(Note.created_at.desc(), Note.id.desc()).limit(NOTES).all()
    solved_in = {}
    for programme, _ in mine:
        solved_in[programme.channel_id] = solved_in.get(programme.channel_id, 0) + 1
    return {
        "score": score, "place": place, "of": of, "solves": len(mine),
        "hints_used": Unlocks.query.filter_by(account_id=team.id).count(),
        "instances_live": live,
        "members": _members(team, viewer, credited),
        "bonus": int(bonus or 0),
        "notes": [{"title": note.title, "content": note.text} for note in notes],
        "by_channel": [
            {
                "channel": channel.id, "name": channel.name,
                "sponsor": {"name": channel.sponsor_name} if channel.kind == "sponsored" else None,
                "solved": solved_in.get(channel.id, 0), "total": len(view.by_channel.get(channel.id, [])),
            }
            for channel in view.channels
        ],
    }


def _story(viewer, view):
    """The meter: `reels` (the studio's solves, up to `reels_needed`) and `on_air` (every studio's solves over the crew's target). None when off."""
    if not story_meter():
        return None
    needed = 0 if view.before else max(1, math.ceil(len(view.rows) / 3))
    mine = sum(1 for _, challenge in view.rows if challenge.id in view.shown and challenge.id in viewer.solved)
    target = story_air_target()
    on_air = 0
    if target and not view.before:
        known = counts.for_viewer(viewer)
        every = sum(int(known.get(challenge.id, 0)) for _, challenge in view.rows if challenge.id in view.shown)
        on_air = figures.js_round(min(1, every / target) * 100) / 100
        on_air = int(on_air) if on_air == int(on_air) else on_air
    return {"reels": min(needed, mine), "reels_needed": needed, "on_air": on_air}


def meter(viewer):
    """The Guide's `story` block alone (what `build` puts under `story`), for a viewer; the finale's gate needs only this."""
    return _story(viewer, plan.read(viewer, current_phase()))


def build(viewer):
    """-> (data, etag) for a viewer (see `viewer.current`). The ETag is over the data with the tick left out."""
    ver = signature()  # first: the page must never carry a tick newer than its data (see the note in board.build)
    phase = current_phase()
    view = plan.read(viewer, phase)
    data = {
        "ver": ver, "phase": {"state": phase.state, "frozen": phase.frozen}, "banner": banner.for_phase(phase),
        "epg_sig": epg.signature(epg.model(view, viewer)),
        "team": _team_block(viewer, phase, view) if viewer.team is not None else None,
        "story": _story(viewer, view),
    }
    return data, envelope.strong_etag("g", {**data, "ver": 0})
