"""Who may do what to a studio: renaming (policy A), removing a member, and rejoining after a removal.

The owner's answers of 19 Nov 2026 ("go with your recommendation"):

  Rename. A captain may rename the studio once a day. The name is locked for good from the real start (28 Nov 2026, 10:00 IST) or after the studio's first solve,
  whichever comes first. The crew can always rename. Every change leaves a line in the activity ledger with the old and the new name (activity.py sees it wherever
  it comes from). The rule is enforced on BOTH ways a captain can rename: the form (POST /team) and the API (PATCH /api/v1/teams/me).

  Removal. A captain unlinks a member (`team_id = None`). What the member earned STAYS with the studio (solves, submissions, hint unlocks, awards): CTFd's own
  member removal (admin only, `DELETE /api/v1/teams/<id>/members`) deletes all of it and must never be given to a captain. The captain, and oneself, cannot be
  removed; at most two removals per studio per day; the removed person cannot join the same studio again until the crew lifts the removal (an old invite link
  would otherwise bring them straight back).

All the words a player can read are plain sentences. The refusals return text; the callers decide how to show it.
"""
import datetime
import math

from flask import abort, g, request
from sqlalchemy import select

from CTFd.models import Solves, Unlocks, Users, db
from CTFd.plugins.l3mon_core import activity, audit
from CTFd.plugins.l3mon_core.clock import ist_to_epoch
from CTFd.plugins.l3mon_core.models import Activity, Removal
from CTFd.utils import get_config

RENAME_LOCK_KEY = "l3mon_rename_locked_from"
REMOVALS_KEY = "l3mon_removals_per_day"
DEFAULT_RENAME_LOCKED_FROM = ist_to_epoch(2026, 11, 28, 10)  # the real start: 10:00 IST on 28 November 2026
DEFAULT_REMOVALS_PER_DAY = 2
DAY = datetime.timedelta(hours=24)
_MIN_EPOCH, _MAX_EPOCH = 1_577_836_800, 2_500_000_000  # 2020 and 2049, like the finale time


def _utcnow():
    return datetime.datetime.utcnow()


def _bounded_int(value, default, low, high):
    if value is None or isinstance(value, bool):
        return default
    try:
        number = int(str(value).strip())
    except (TypeError, ValueError):
        return default
    return number if low <= number <= high else default


def rename_locked_from() -> int:
    """UTC epoch seconds from which a captain can no longer rename a studio. The crew can move it (the setting `l3mon_rename_locked_from`)."""
    return _bounded_int(get_config(RENAME_LOCK_KEY), DEFAULT_RENAME_LOCKED_FROM, _MIN_EPOCH, _MAX_EPOCH)


def removals_per_day() -> int:
    """How many members one studio's captain may remove in a day (the setting `l3mon_removals_per_day`)."""
    return _bounded_int(get_config(REMOVALS_KEY), DEFAULT_REMOVALS_PER_DAY, 1, 50)


def _is_crew(user) -> bool:
    return getattr(user, "type", None) == "admin"


def _hours(delta) -> str:
    hours = max(1, math.ceil(delta.total_seconds() / 3600))
    return f"{hours} hour" + ("" if hours == 1 else "s")


# ------------------------------------------------------------------ renaming

def rename_refusal(team, new_name, actor, now=None):
    """A plain sentence saying why `actor` may not give `team` the name `new_name`, or None when it is allowed."""
    if _is_crew(actor) or new_name == team.name:
        return None
    now = now or _utcnow()
    if now >= datetime.datetime.utcfromtimestamp(rename_locked_from()):
        return "Studio names are locked: the broadcast has started. If the name is a problem, ask the crew."
    if db.session.query(Solves.id).filter(Solves.team_id == team.id).first():
        return "Your studio has already scored, so its name is locked. If the name is a problem, ask the crew."
    # Only the studio's own people use up the day: a rename by the crew (or by the platform itself, with nobody signed in) is not the captain's rename.
    crew_ids = select(Users.id).where(Users.type == "admin")
    last = (
        Activity.query.filter(Activity.kind == "studio.rename", Activity.team_id == team.id, Activity.at > now - DAY, Activity.actor_id.isnot(None), Activity.actor_id.notin_(crew_ids))
        .order_by(Activity.at.desc())
        .first()
    )
    if last is not None:
        return f"A studio can be renamed once a day. You can rename it again in {_hours(last.at + DAY - now)}."
    return None


# ------------------------------------------------------------------ removing a member

def removal_refusal(team, target, captain, now=None):
    """A plain sentence saying why `captain` may not remove `target` from `team`, or None when it is allowed."""
    if target is None or target.team_id != team.id:
        return "That person is not in your studio."
    if target.id == captain.id:
        return "You cannot remove yourself." + (" The captain's chair has to be handed over to another member first." if target.id == team.captain_id else "")
    if target.id == team.captain_id:
        return "The captain cannot be removed. Hand the captain's chair over first."
    now = now or _utcnow()
    count = Removal.query.filter(Removal.team_id == team.id, Removal.removed_at > now - DAY).count()
    limit = removals_per_day()
    if count >= limit:
        return f"A studio can remove up to {limit} members a day. Try again tomorrow, or ask the crew."
    return None


def _had_recent_activity(user, team_id, now) -> bool:
    """A solve or a hint unlock by this member in the day before the removal: the 'lend my flag, then leave' pattern the crew should look at."""
    since = now - DAY
    solved = db.session.query(Solves.id).filter(Solves.user_id == user.id, Solves.team_id == team_id, Solves.date >= since).first()
    unlocked = db.session.query(Unlocks.id).filter(Unlocks.user_id == user.id, Unlocks.team_id == team_id, Unlocks.date >= since).first()
    return bool(solved or unlocked)


def remove_member(team, target, by, now=None) -> Removal:
    """Unlink `target` from `team`. Nothing the member earned is touched. The caller has already checked removal_refusal and commits."""
    now = now or _utcnow()
    recent = _had_recent_activity(target, team.id, now)
    row = Removal(team_id=team.id, user_id=target.id, removed_by=getattr(by, "id", None), removed_at=now, recent_activity=1 if recent else 0)
    db.session.add(row)
    target.team_id = None
    activity.queue("studio.remove", actor=by, team_id=team.id, target=f"user:{target.id}", detail={"name": target.name, "recent_activity": recent})
    if recent:
        audit.record("studio.remove.recent", f"user:{target.id}", f"{target.name} had a solve or a hint unlock in the last day and was removed from {team.name}", actor=by)
    return row


# ------------------------------------------------------------------ rejoining

def joining_refusal(user_id, team_id):
    """A plain sentence when this person was removed from this studio and the crew has not lifted it, else None."""
    row = Removal.query.filter_by(user_id=user_id, team_id=team_id, lifted_at=None).first()
    if row is None:
        return None
    return "You were removed from this studio by its captain. If that was a mistake, ask the crew."


def lift(removal, by) -> None:
    """The crew lets the person join the studio again."""
    removal.lifted_at = _utcnow()
    removal.lifted_by = getattr(by, "id", None)
    activity.queue("studio.removal.lift", actor=by, team_id=removal.team_id, target=f"user:{removal.user_id}")


# ------------------------------------------------------------------ the two ways in that are not the theme's own form

JOIN = "teams.join"
INVITE = "teams.invite"
API_OWN_STUDIO = "api.teams_team_private"


def guard_before_the_handler():
    """before_request. (1) A removed person is turned away from the studio they were removed from, whether they use its name and password or an old invite link.
    (2) The rename policy on the API path (the form path checks it itself). Registered after player_locks, so the studio's row lock is already held."""
    from CTFd.utils.user import get_current_team, get_current_user, is_admin
    from flask import jsonify, session

    if request.endpoint in (JOIN, INVITE) and session.get("id") and not is_admin():
        from CTFd.plugins.l3mon_core.player_locks import _studio_named, _studio_of_invite

        if request.endpoint == JOIN and request.method == "POST":
            team_id = _studio_named(request.form.get("name"))
        elif request.endpoint == INVITE:
            team_id = _studio_of_invite(request.args.get("code"))
        else:
            team_id = None
        if team_id is not None:
            reason = joining_refusal(session["id"], team_id)
            if reason:
                g.l3mon_join_refused = reason
                activity.record("studio.join.refused", actor=session["id"], team_id=team_id, target=f"team:{team_id}", detail={"why": "removed"})
                abort(403)
        return None

    if request.method == "PATCH" and request.endpoint == API_OWN_STUDIO and session.get("id") and not is_admin():
        body = request.get_json(silent=True)
        team, user = get_current_team(), get_current_user()
        if isinstance(body, dict) and "name" in body and team is not None and user is not None and user.id == team.captain_id:
            from CTFd.plugins.l3mon_core.player_locks import lock_studio

            lock_studio(team.id)  # like the form: two renames at once must not both pass the once-a-day rule (the lock lasts until CTFd's handler commits)
            reason = rename_refusal(team, body["name"], user)
            if reason:
                activity.record("studio.rename.refused", actor=user, team_id=team.id, target=f"team:{team.id}", detail={"asked": body["name"], "via": "api"})
                response = jsonify({"success": False, "errors": {"name": [reason]}})
                response.status_code = 403
                return response
    return None


def install(app) -> None:
    if "l3mon_studio_rules" in app.extensions:
        return
    app.extensions["l3mon_studio_rules"] = True
    app.before_request(guard_before_the_handler)
