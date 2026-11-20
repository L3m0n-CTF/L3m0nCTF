"""The activity ledger: everything a participant did, written so that it can be trusted afterwards (the owner's rule of 19 Nov 2026: critical).

    activity.record(kind, actor=..., team_id=..., target=..., detail={...})   write one line now
    activity.queue(kind, ...)                                                 write it when the surrounding transaction COMMITS (dropped on rollback)
    activity.verify_chain()                                                   recompute the whole chain; says where it breaks
    activity.triggers_installed()                                             does the database refuse UPDATE and DELETE?

Four properties, each with a test (tests/test_activity.py):
  * append-only: UPDATE and DELETE on l3mon_activity are refused by database triggers (see models.Activity for why they are created in a separate step on MariaDB);
  * chained: every row carries the hash of the row before it, so a changed or a missing row is found;
  * a second copy: every line is also printed as ONE JSON line on standard output, which the log shipper takes to Loki; the database cannot erase that copy;
  * harmless: a ledger failure is printed as a line marked "lost" and the caller carries on. Logging must never stop a player from playing.

Written in its OWN short transaction (a separate connection), never in the request's: a request that is slow (a call to a challenge host, say) must not hold the
chain, and the chain's row lock lasts a few milliseconds. Lines that describe a database change (a rename, a join) are queued and written after the commit that
made the change real, so a rolled-back action leaves no line.

Never recorded, by construction: passwords, tokens, CSRF nonces, cookies, and any flag text (`L3m0n{...}`), whatever the caller passes.
"""
import datetime
import hashlib
import json
import logging
import re
import sys
from collections import namedtuple

from sqlalchemy import event, inspect, text

from CTFd.models import Teams, Users, db
from CTFd.plugins.l3mon_core.models import Activity

GENESIS = "0" * 64
UNSET = object()
SYSTEM = "system"
ChainResult = namedtuple("ChainResult", "ok checked first_bad_id reason")

_LIMITS = {"kind": 40, "actor_name": 80, "target": 100, "detail": 1000, "ip": 45, "user_agent": 200, "request_id": 64}
_SECRET_KEY = re.compile(r"pass|token|secret|csrf|nonce|cookie|authorization|flag|key|session", re.IGNORECASE)
_FLAG_TEXT = re.compile(r"L3m0n\{[^}]*\}")
_HIDDEN = "[hidden]"
_PENDING = "l3mon_activity_pending"
_ECHOES = "l3mon_activity_echoes"
_log = logging.getLogger("l3mon")
_installed = False


def _clip(value, limit) -> str:
    value = "" if value is None else str(value)
    return value if len(value) <= limit else value[: limit - 1] + "…"


def _scrub(value):
    """A detail made safe to store: secret-looking keys hidden, flag text masked, everything else turned into plain JSON types."""
    if isinstance(value, dict):
        return {str(k): (_HIDDEN if _SECRET_KEY.search(str(k)) else _scrub(v)) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_scrub(v) for v in value]
    if isinstance(value, bool) or value is None or isinstance(value, (int, float)):
        return value
    return _FLAG_TEXT.sub(_HIDDEN, str(value))


def _detail_text(detail) -> str:
    if detail in (None, "", {}):
        return ""
    encoded = json.dumps(_scrub(detail), sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    if len(encoded) <= _LIMITS["detail"]:
        return encoded
    return json.dumps({"clipped": encoded[: _LIMITS["detail"] - 40]}, ensure_ascii=False, separators=(",", ":"))[: _LIMITS["detail"]]


def _request_fields() -> dict:
    """The address, browser and request id of the current request, without touching the database."""
    try:
        from flask import has_request_context, request

        if not has_request_context():
            return {"ip": "", "user_agent": "", "request_id": ""}
        try:
            from CTFd.utils.user import get_ip

            ip = get_ip()
        except Exception:
            ip = request.remote_addr or ""
        return {
            "ip": _clip(ip, _LIMITS["ip"]),
            "user_agent": _clip(request.headers.get("User-Agent", ""), _LIMITS["user_agent"]),
            "request_id": _clip(request.headers.get("X-Request-ID", ""), _LIMITS["request_id"]),
        }
    except Exception:
        return {"ip": "", "user_agent": "", "request_id": ""}


def _session_user_id():
    """Who the request says it is, from the signed cookie only (no query: safe inside a database flush)."""
    try:
        from flask import has_request_context, session

        return session.get("id") if has_request_context() else None
    except Exception:
        return None


def _resolve_actor(actor):
    """(id, name). Called when it is safe to query."""
    if actor is UNSET:
        uid = _session_user_id()
        actor = Users.query.filter_by(id=uid).first() if uid else None
    if isinstance(actor, int):
        actor = Users.query.filter_by(id=actor).first()
    if actor is None:
        return None, SYSTEM
    return getattr(actor, "id", None), _clip(getattr(actor, "name", None) or SYSTEM, _LIMITS["actor_name"])


def _actor_inside_a_flush(actor):
    """(id, name) for a line queued while the database is being written. The session may not run queries after the commit (SQLAlchemy forbids it in after_commit),
    so the name is read NOW, with a plain SELECT on the session's own connection (read only, and allowed inside a flush)."""
    if actor is None:
        return None, SYSTEM
    if hasattr(actor, "id"):
        return actor.id, _clip(getattr(actor, "name", None) or SYSTEM, _LIMITS["actor_name"])
    try:
        name = db.session.execute(text("SELECT name FROM users WHERE id = :i"), {"i": actor}).scalar()
    except Exception:
        name = None
    return actor, _clip(name or f"user {actor}", _LIMITS["actor_name"])


def _fields(kind, actor, team_id, target, detail, resolve_now=True, uid_hint=None) -> dict:
    fields = {
        "kind": _clip(kind, _LIMITS["kind"]),
        "team_id": team_id,
        "target": _clip(target, _LIMITS["target"]),
        "detail": _detail_text(detail),
        **_request_fields(),
    }
    if resolve_now:
        fields["actor_id"], fields["actor_name"] = _resolve_actor(actor)
    else:  # inside a flush: who it was is read now, never after the commit
        fields["actor_id"], fields["actor_name"] = _actor_inside_a_flush(actor if actor is not UNSET else (uid_hint or _session_user_id()))
    return fields


def _canonical(at, f) -> str:
    return json.dumps(
        [at.strftime("%Y-%m-%dT%H:%M:%S"), f["kind"], f["actor_id"], f["actor_name"], f["team_id"], f["target"], f["detail"], f["ip"], f["user_agent"], f["request_id"]],
        ensure_ascii=False, separators=(",", ":"),
    )


def _digest(prev_hash, at, f) -> str:
    return hashlib.sha256((prev_hash + "\n" + _canonical(at, f)).encode("utf-8")).hexdigest()


def _append(conn, f, at):
    lock = " FOR UPDATE" if conn.dialect.name in ("mysql", "mariadb") else ""
    previous = conn.execute(text("SELECT hash FROM l3mon_activity ORDER BY id DESC LIMIT 1" + lock)).scalar() or GENESIS
    digest = _digest(previous, at, f)
    result = conn.execute(Activity.__table__.insert().values(at=at, prev_hash=previous, hash=digest, **{k: v for k, v in f.items() if not k.startswith("_")}))
    return result.inserted_primary_key[0], at, digest


def _write(f, conn=None):
    """Append one row to the chain: in the caller's transaction when `conn` is given, else in its own short one. Returns (id, at, hash)."""
    at = datetime.datetime.utcnow().replace(microsecond=0)
    if conn is not None:
        return _append(conn, f, at)
    with db.engine.begin() as own:
        return _append(own, f, at)


def _echo(f, row_id=None, at=None, digest=None, lost=False, error=None) -> None:
    """The second copy: one JSON line on standard output."""
    line = {"l3mon_activity": 1, "id": row_id, "at": at.strftime("%Y-%m-%dT%H:%M:%SZ") if at else None, **{k: v for k, v in f.items() if not k.startswith("_")}}
    try:
        line["detail"] = json.loads(f["detail"]) if f.get("detail") else {}
    except ValueError:
        pass
    if digest:
        line["hash"] = digest
    if lost:
        line["lost"] = True
        line["error"] = error
        line["at"] = datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
    print(json.dumps(line, ensure_ascii=False, separators=(",", ":")), file=sys.stdout, flush=True)


def _commit_line(f):
    try:
        row_id, at, digest = _write(f)
    except Exception as err:  # never into the caller
        _echo(f, lost=True, error=type(err).__name__)
        _log.error("l3mon: the activity ledger could not be written (%s): %s", f.get("kind"), type(err).__name__)
        return None
    _echo(f, row_id, at, digest)
    return row_id


def record(kind, *, actor=UNSET, team_id=None, target="", detail=None):
    """Write one line now. `actor` is a Users row or id; by default the signed-in person of this request, and `None` says the platform itself did it.
    Returns the id of the row, or None when the ledger could not be written (the line is then printed with "lost": true)."""
    try:
        fields = _fields(kind, actor, team_id, target, detail)
    except Exception as err:
        fields = {"kind": _clip(kind, _LIMITS["kind"]), "team_id": team_id, "target": _clip(target, _LIMITS["target"]), "detail": "", "actor_id": None, "actor_name": SYSTEM, "ip": "", "user_agent": "", "request_id": ""}
        _log.error("l3mon: the activity ledger could not read the request (%s)", type(err).__name__)
    return _commit_line(fields)


def queue(kind, *, actor=UNSET, team_id=None, target="", detail=None):
    """Write the line when the surrounding transaction commits; drop it when it rolls back. Safe to call inside a database flush (it makes no query)."""
    db.session.info.setdefault(_PENDING, []).append(_fields(kind, actor, team_id, target, detail, resolve_now=False))


def record_in_transaction(connection, kind, *, actor=UNSET, team_id=None, target="", detail=None):
    """Write the line in the transaction of the change it describes (`connection` is the one the database flush is using), so that it exists exactly when the change
    does, and is already there when the studio's row lock is released: the once-a-day rename rule reads the ledger, and a line written after the commit left a few
    milliseconds in which a second request, waiting for that lock, could read the ledger too early (a review finding of 20 Nov 2026). Its copy on standard output is
    printed after the commit (and never when the change is rolled back). Use it only for rare events: the chain's row lock is held until the commit."""
    fields = _fields(kind, actor, team_id, target, detail, resolve_now=False)
    row_id, at, digest = _write(fields, conn=connection)
    db.session.info.setdefault(_ECHOES, []).append((fields, row_id, at, digest))
    return row_id


def _after_commit(session):
    for fields, row_id, at, digest in session.info.pop(_ECHOES, None) or ():
        _echo(fields, row_id, at, digest)
    pending = session.info.pop(_PENDING, None)
    for fields in pending or ():
        _commit_line(fields)  # complete already: no query may be run here


def _forget(session, *_):
    session.info.pop(_PENDING, None)
    session.info.pop(_ECHOES, None)


# ------------------------------------------------------------------ studio changes seen wherever they come from

def _history(target, attribute):
    h = inspect(target).attrs[attribute].history
    old = h.deleted[0] if h.deleted else None
    new = h.added[0] if h.added else None
    return h.has_changes(), old, new


def _studio_updated(mapper, connection, team):
    changed, old, new = _history(team, "name")
    if changed and old is not None and old != new:
        record_in_transaction(connection, "studio.rename", team_id=team.id, target=f"team:{team.id}", detail={"old": old, "new": new})
    changed, old, new = _history(team, "captain_id")
    if changed and old != new:
        queue("studio.captain", team_id=team.id, target=f"user:{new}" if new else "", detail={"old_captain": old, "new_captain": new})


def _studio_created(mapper, connection, team):
    queue("studio.create", team_id=team.id, target=f"team:{team.id}", detail={"name": team.name})


def _member_updated(mapper, connection, user):
    changed, old, new = _history(user, "team_id")
    if not changed or old == new:
        return
    if old is None and new:
        queue("studio.join", actor=user.id, team_id=new, target=f"user:{user.id}")
    elif old and new is None:
        queue("studio.leave", team_id=old, target=f"user:{user.id}")
    elif old and new:
        queue("studio.switch", team_id=new, target=f"user:{user.id}", detail={"from": old, "to": new})


# ------------------------------------------------------------------ the chain and the triggers

def verify_chain(batch=1000) -> ChainResult:
    """Recompute every hash from the first row. The result names the first row that does not match (changed itself, or not following its predecessor: a row is missing)."""
    previous, checked, last_id = GENESIS, 0, 0
    while True:
        rows = db.session.query(Activity).filter(Activity.id > last_id).order_by(Activity.id).limit(batch).all()
        if not rows:
            return ChainResult(True, checked, None, "")
        for row in rows:
            fields = {k: getattr(row, k) for k in ("kind", "actor_id", "actor_name", "team_id", "target", "detail", "ip", "user_agent", "request_id")}
            if row.prev_hash != previous:
                return ChainResult(False, checked, row.id, "a row before this one is missing or was changed")
            if row.hash != _digest(previous, row.at, fields):
                return ChainResult(False, checked, row.id, "this row was changed")
            previous, checked, last_id = row.hash, checked + 1, row.id
        db.session.expire_all()


def triggers_installed() -> bool:
    """True when the database refuses UPDATE and DELETE on the ledger (both triggers exist)."""
    names = "('l3mon_activity_no_update', 'l3mon_activity_no_delete')"
    if db.engine.dialect.name == "sqlite":
        sql = f"SELECT count(*) FROM sqlite_master WHERE type = 'trigger' AND name IN {names}"
    else:
        sql = f"SELECT count(*) FROM information_schema.TRIGGERS WHERE TRIGGER_SCHEMA = DATABASE() AND TRIGGER_NAME IN {names}"
    return db.session.execute(text(sql)).scalar() == 2


def install() -> None:
    """Register the hooks once: deferred lines follow their commit, and studio changes are seen wherever they come from (the form, the API, the crew)."""
    global _installed
    if _installed:
        return
    _installed = True
    # To say what a rename or a leave changed FROM, the old value must be known when the new one is set. SQLAlchemy skips loading it by default (a row that was
    # committed since has its fields expired), and the history then has no "old" and the change would go unseen. active_history loads it at the moment of the
    # change (one small SELECT), outside the flush.
    for attribute in (Teams.name, Teams.captain_id, Users.team_id):
        attribute.impl.active_history = True
    event.listen(db.session, "after_commit", _after_commit)
    event.listen(db.session, "after_rollback", _forget)
    event.listen(db.session, "after_soft_rollback", _forget)
    event.listen(Teams, "before_update", _studio_updated)
    event.listen(Teams, "after_insert", _studio_created)
    event.listen(Users, "before_update", _member_updated)
