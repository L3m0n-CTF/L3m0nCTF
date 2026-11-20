"""The crew's side of the activity ledger and the removals (see docs/runbooks/17-activity-log.md).

    GET  /api/v1/l3mon/admin/removals[?team_id=&active=1]   who was removed from which studio, by whom, when; `active=1` is the ones still in force
    POST /api/v1/l3mon/admin/removals/<id>/lift             let the person join that studio again (recorded)
    GET  /api/v1/l3mon/admin/activity[?team_id=&actor_id=&kind=&limit=]   the ledger, newest first (at most 500 lines)
    GET  /api/v1/l3mon/admin/activity/verify                recompute the hash chain; say whether the database refuses changes

Administrator surface twice over: nginx answers /api/v1/l3mon/admin/ only for the organisers' addresses, and @admins_only checks the session or token.
The reader never sees the hashes (they are for the verifier).
"""
import json

from flask import Blueprint, jsonify, render_template, request

from CTFd.models import Teams, Users, db
from CTFd.plugins.l3mon_core import activity, audit, studio_rules
from CTFd.plugins.l3mon_core.models import Activity, Removal
from CTFd.utils.decorators import admins_only
from CTFd.utils.user import get_current_user

bp = Blueprint("l3mon_core_admin", __name__, url_prefix="/api/v1/l3mon/admin")
page_bp = Blueprint("l3mon_core_page", __name__, template_folder="templates")
MAX_LINES = 500
DEFAULT_LINES = 100


def _iso(moment):
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ") if moment else None


def _int_arg(name):
    """The integer query argument, None when absent. A value that is not a whole number is a 400 for the caller to return."""
    raw = request.args.get(name)
    if raw is None or raw == "":
        return None
    return int(raw)


def _bad(message):
    response = jsonify({"success": False, "errors": {"": [message]}})
    response.status_code = 400
    return response


@page_bp.route("/admin/l3mon/activity")
@admins_only
def activity_page():
    """The crew's page: filter the ledger, check the chain, export, see the removals and lift one. The script is /plugins/l3mon_core/assets/activity.js."""
    return render_template("l3mon_core/activity.html")


@bp.route("/removals", methods=["GET"])
@admins_only
def list_removals():
    try:
        team_id = _int_arg("team_id")
    except ValueError:
        return _bad("team_id must be a whole number")
    query = Removal.query.order_by(Removal.id.desc())
    if team_id is not None:
        query = query.filter(Removal.team_id == team_id)
    if request.args.get("active") in ("1", "true", "yes"):
        query = query.filter(Removal.lifted_at.is_(None))
    rows = query.limit(MAX_LINES).all()
    teams = {t.id: t.name for t in Teams.query.filter(Teams.id.in_({r.team_id for r in rows})).all()} if rows else {}
    users = {u.id: u.name for u in Users.query.filter(Users.id.in_({r.user_id for r in rows})).all()} if rows else {}
    return jsonify({"success": True, "data": [
        {
            "id": r.id, "team_id": r.team_id, "team_name": teams.get(r.team_id), "user_id": r.user_id, "user_name": users.get(r.user_id),
            "removed_by": r.removed_by, "removed_at": _iso(r.removed_at), "recent_activity": bool(r.recent_activity),
            "lifted_at": _iso(r.lifted_at), "lifted_by": r.lifted_by,
        }
        for r in rows
    ]})


@bp.route("/removals/<int:removal_id>/lift", methods=["POST"])
@admins_only
def lift_removal(removal_id):
    removal = Removal.query.get(removal_id)
    if removal is None:
        return jsonify({"success": False, "errors": {"": ["No such removal."]}}), 404
    if removal.lifted_at is not None:
        return jsonify({"success": False, "errors": {"": ["That removal was already lifted."]}}), 409
    studio_rules.lift(removal, get_current_user())
    audit.record("studio.removal.lift", f"user:{removal.user_id}", f"the crew let user {removal.user_id} back into studio {removal.team_id}")
    db.session.commit()
    return jsonify({"success": True})


@bp.route("/activity", methods=["GET"])
@admins_only
def read_activity():
    try:
        team_id, actor_id, limit = _int_arg("team_id"), _int_arg("actor_id"), _int_arg("limit")
    except ValueError:
        return _bad("team_id, actor_id and limit must be whole numbers")
    limit = DEFAULT_LINES if limit is None else max(1, min(limit, MAX_LINES))
    query = Activity.query.order_by(Activity.id.desc())
    if team_id is not None:
        query = query.filter(Activity.team_id == team_id)
    if actor_id is not None:
        query = query.filter(Activity.actor_id == actor_id)
    kind = request.args.get("kind")
    if kind:
        query = query.filter(Activity.kind == kind)
    data = []
    for row in query.limit(limit).all():
        try:
            detail = json.loads(row.detail) if row.detail else {}
        except ValueError:
            detail = {"raw": row.detail}
        data.append({
            "id": row.id, "at": _iso(row.at), "kind": row.kind, "actor_id": row.actor_id, "actor_name": row.actor_name, "team_id": row.team_id,
            "target": row.target, "detail": detail, "ip": row.ip, "user_agent": row.user_agent, "request_id": row.request_id,
        })
    return jsonify({"success": True, "data": data})


@bp.route("/activity/verify", methods=["GET"])
@admins_only
def verify_activity():
    result = activity.verify_chain()
    return jsonify({"success": True, "data": {
        "ok": result.ok, "checked": result.checked, "first_bad_id": result.first_bad_id, "reason": result.reason,
        "triggers_installed": activity.triggers_installed(),
    }})
