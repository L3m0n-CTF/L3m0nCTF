"""The crew's accounts are hidden (found by the end-to-end test of 15 November).

CTFd lists every account that is not `hidden` to every signed-in player (`GET /api/v1/users`, a user's page, the search by name). The
administrator that CTFd's set-up page creates is hidden, but the one made from the preset in the settings is not, and neither is
one made later through the admin API or by promoting a player: so a player could read the crew's account name and open its page.
Only the name leaked (no e-mail, no scores), but it is the name somebody would try to guess a password for.

Every account of the type `admin` is now saved hidden, whatever way it was made (a mapper event on the user table), and the
accounts that already exist are corrected when the platform starts. A crew member who is also a player is not a case this platform
has: an administrator has no studio.
"""
from sqlalchemy import event

from CTFd.models import Users, db

_installed = False


def _hide_before_save(mapper, connection, target) -> None:
    if target.type == "admin" and not target.hidden:
        target.hidden = True


def hide_existing_admins() -> int:
    """Hide the administrators that are listed now. Returns how many were changed."""
    changed = db.session.query(Users).filter(Users.type == "admin", Users.hidden == False).update(  # noqa: E712 (SQL, not Python)
        {"hidden": True}, synchronize_session=False
    )
    db.session.commit()
    return changed


def install() -> None:
    global _installed
    if not _installed:
        _installed = True
        event.listen(Users, "before_insert", _hide_before_save, propagate=True)
        event.listen(Users, "before_update", _hide_before_save, propagate=True)
    try:
        hide_existing_admins()
    except Exception:  # the table is there when plugins load; if it ever is not, the event above still covers every later save
        db.session.rollback()
