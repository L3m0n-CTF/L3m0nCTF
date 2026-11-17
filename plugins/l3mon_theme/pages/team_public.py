"""The public studio profile page (/teams/<id>) for the theme (Annex 1 Bug 2).

Provides:
- View function for GET /teams/<int:team_id>
- Displays studio name, standing, TRP, crew members, and solved programmes with IST timestamps.
"""
import datetime
import logging
from flask import abort, redirect, render_template, request, url_for

from CTFd.models import Challenges, Solves, Teams, Users, db
from CTFd.plugins.l3mon_board import board, viewer
from CTFd.plugins.l3mon_core.clock import current_phase, window
from CTFd.plugins.l3mon_core.visibility import visible_challenge_ids
from CTFd.utils.countries import lookup_country_code
from CTFd.utils.dates import unix_time_to_utc
from CTFd.utils.user import get_current_user, is_admin

_log = logging.getLogger("l3mon.theme.team_public")


def _ordinal(n: int) -> str:
    if 11 <= (n % 100) <= 13:
        return f"{n}th"
    suffix = {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


def public_team_profile(team_id: int):
    """Render the themed public studio profile for team_id."""
    no = viewer.refusal()
    if no is not None:
        code = (no.get_json(silent=True) or {}).get("error")
        if code == "auth_required":
            next_url = request.full_path.rstrip("?") if not request.query_string else request.full_path
            return redirect(url_for("auth.login", next=next_url))
        if code == "unverified":
            return redirect(url_for("auth.confirm"))
        if code == "no_team":
            return redirect(url_for("teams.private"))
        abort(403)

    is_crew = is_admin()
    if is_crew:
        target_team = Teams.query.filter_by(id=team_id).first_or_404()
    else:
        target_team = Teams.query.filter_by(id=team_id, banned=False, hidden=False).first_or_404()

    phase = current_phase()
    score, place, of = board.numbers(target_team, phase)

    if phase.frozen:
        standing = f"Ranks frozen · {score} TRP"
    elif place is not None:
        standing = f"{_ordinal(place)} of {of} · {score} TRP"
    else:
        standing = f"Not ranked yet · {score} TRP"

    country_display = lookup_country_code(target_team.country) if target_team.country else None
    meta_parts = [p for p in (country_display, target_team.affiliation) if p]
    meta_text = " · ".join(meta_parts)

    user_rows = (
        db.session.query(Users.id, Users.name)
        .filter(Users.team_id == target_team.id, Users.banned.is_(False))
        .all()
    )
    members_data = []
    for uid, uname in user_rows:
        is_cap = (uid == target_team.captain_id)
        tag = "captain" if is_cap else ""
        members_data.append({
            "id": str(uid),
            "name": uname,
            "ini": uname[:1].upper() if uname else "P",
            "tag": tag,
            "is_captain": is_cap,
        })
    members_data.sort(key=lambda m: (not m["is_captain"], m["name"].casefold()))

    view_asker = viewer.current()
    shown = visible_challenge_ids(solved_ids=view_asker.solved) if not is_crew else None

    freeze = window().freeze
    freeze_time = unix_time_to_utc(freeze) if freeze else None
    hide_late = (not is_crew) and (view_asker.team is None or view_asker.team.id != target_team.id) and (freeze_time is not None)

    solves_query = (
        db.session.query(
            Solves.challenge_id,
            Solves.date,
            Challenges.name,
            Challenges.category,
            Challenges.value,
            Users.name,
        )
        .join(Challenges, Solves.challenge_id == Challenges.id)
        .join(Users, Solves.user_id == Users.id)
        .filter(Solves.account_id == target_team.id)
        .order_by(Solves.date.desc())
    )
    solves_rows = solves_query.all()

    solves_data = []
    for cid, sdate, cname, ccat, cval, solver_name in solves_rows:
        if shown is not None and cid not in shown:
            continue
        if hide_late and sdate and sdate >= freeze_time:
            continue

        ist_time = sdate + datetime.timedelta(hours=5, minutes=30) if sdate else None
        time_str = ist_time.strftime("%d %b, %H:%M IST") if ist_time else ""

        solves_data.append({
            "name": cname or "Programme",
            "category": ccat or "",
            "trp": int(cval or 0),
            "solver": solver_name or "Teammate",
            "time": time_str,
        })

    current_user = get_current_user()
    is_own_team = bool(current_user and current_user.team_id == target_team.id)

    public_context = {
        "id": str(target_team.id),
        "name": target_team.name,
        "meta": meta_text,
        "standing": standing,
        "score": str(score),
        "members": members_data,
        "solves": solves_data,
        "solves_count": len(solves_data),
        "is_own": is_own_team,
    }

    return render_template("teams/public.html", tm=public_context, team=target_team)


def install(app):
    """Register public studio profile view for /teams/<int:team_id>."""
    if "teams.public" in app.view_functions:
        app.view_functions["teams.public"] = public_team_profile
    else:
        app.add_url_rule("/teams/<int:team_id>", endpoint="teams.public", view_func=public_team_profile, methods=["GET"])
