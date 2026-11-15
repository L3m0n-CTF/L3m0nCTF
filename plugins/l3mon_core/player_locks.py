"""Row locks for the player actions that read a limit and then write (found by the end-to-end test of 15 November).

CTFd's own handlers check first and write afterwards, with nothing between the two. Two requests that arrive together both pass the
check, and then both write:

- joining a studio (a join with the name and password, or an invite link): nine players pressing Join at the same moment all saw eight
  members and the studio ended with ten, over the limit of the event;
- unlocking a hint: three requests together were each told "you can afford it" and each took the cost, so the studio's score went
  below nothing.

The fix is a lock on the studio's row (SELECT ... FOR UPDATE) taken BEFORE CTFd's handler starts. Whoever holds it runs the whole
handler against the data as it stands; the next request waits, then reads what the first one wrote. The lock ends with the first
commit of the handler (and CTFd commits exactly once for each of these actions, together with everything the action wrote), or when
the request ends without one.

Same order as `locks.serialize()`: end the open transaction first, so that the locking read is the first statement of a new one, and
the reads of the handler that follow are made after the lock was won, never from a view that is older (MariaDB's default isolation
shows a transaction the data as it was at its first plain read). On SQLite (the tests) there is one writer at a time and the lock
does nothing.

A hint unlock by an account that has no studio is answered here too: CTFd fails with a server error on it (it asks the missing
studio for its score). An administrator is never held up or turned away by this module.
"""
from flask import jsonify, request, session

from CTFd.models import Teams, Users, db

JOIN = "teams.join"
INVITE = "teams.invite"
UNLOCK = "api.unlocks_unlock_list"
LOCKED_ENDPOINTS = (JOIN, INVITE, UNLOCK)


def lock_studio(team_id) -> None:
    """Take the lock on one studio's row. The caller must not hold changes it has not committed: this commits first."""
    db.session.commit()
    db.session.query(Teams.id).filter(Teams.id == team_id).with_for_update().first()


def _studio_named(name):
    if not isinstance(name, str) or not name:
        return None
    row = db.session.query(Teams.id).filter(Teams.name == name).first()
    return row.id if row else None


def _studio_of_invite(code):
    if not code:
        return None
    try:
        return Teams.load_invite_code(code).id
    except Exception:  # an invite that is out of date or not valid: CTFd's handler explains it, there is nothing to lock
        return None


def _studio_of_player(user_id):
    row = db.session.query(Users.team_id).filter(Users.id == user_id).first()
    return row.team_id if row else None


def lock_before_the_handler():
    """before_request: the lock for the three actions above. Anything else passes untouched."""
    if request.method != "POST" or request.endpoint not in LOCKED_ENDPOINTS:
        return None
    user_id = session.get("id")
    if not user_id:
        return None  # a visitor: the view itself turns them away
    from CTFd.utils.user import is_admin

    if is_admin():
        return None

    if request.endpoint == JOIN:
        team_id = _studio_named(request.form.get("name"))
    elif request.endpoint == INVITE:
        team_id = _studio_of_invite(request.args.get("code"))
    else:
        team_id = _studio_of_player(user_id)
        if team_id is None:
            response = jsonify({"success": False, "error": "no_team", "message": "Set up your studio to carry on."})
            response.status_code = 403
            return response
    if team_id is not None:
        lock_studio(team_id)
    return None


def install(app) -> None:
    if "l3mon_player_locks" in app.extensions:
        return
    app.extensions["l3mon_player_locks"] = True
    app.before_request(lock_before_the_handler)
