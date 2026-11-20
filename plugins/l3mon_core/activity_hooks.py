"""What participants do at the door and in their account, written to the activity ledger (docs/runbooks/17-activity-log.md).

One table, `describe()`, says which request means what. It is a plain function of what happened (endpoint, method, status, who was signed in before and after), so it is
tested without a server; `install()` is the thin hook that feeds it and writes what it returns. The hook never changes the response and never raises: logging must never stop
a player from playing.

Sign-in and sign-out are seen as the SESSION changing, so a sign-in with Google or CTFtime counts exactly like one with a password; a refused sign-in is the sign-in form coming
back with errors (or a rate-limit answer). Studio changes (create, join, leave, remove, rename, captain) are seen by activity.py wherever they come from. Flag attempts stay in
CTFd's own submissions table (text, time and address); they are not copied here, so a flag is never in two places.

Never written: a password, a token, the text of a flag, a whole e-mail address (only its domain). Where a field is named, its VALUE is not copied (activity.py also removes anything
secret-looking from any detail).
"""
import hashlib
import hmac
import logging

from flask import current_app, g, request, session
from sqlalchemy import func

from CTFd.plugins.l3mon_core import activity

_log = logging.getLogger("l3mon")

_NEEDS_FORM = {"auth.login", "auth.register", "auth.reset_password"}
_NEEDS_BODY = {"api.unlocks_unlock_list", "l3mon_instances.start_instance", "api.users_user_private"}
_INSTANCE_KINDS = {
    "l3mon_instances.start_instance": "instance.start",
    "l3mon_instances.renew_instance": "instance.renew",
    "l3mon_instances.stop_instance": "instance.stop",
}


def _domain(address):
    address = (address or "").strip()
    return address.rsplit("@", 1)[1].lower() if "@" in address else ""


def _event(kind, actor, target="", detail=None):
    return {"kind": kind, "actor": actor, "target": target, "detail": detail or {}}


def describe(*, endpoint, method, status, view_args, form, body, uid_before, uid_after):
    """The ledger lines (as dictionaries) that this request means, possibly none. `actor` is a user id or None (nobody signed in)."""
    form = form or {}
    events = []
    ok = 200 <= status < 400

    # ---- the session changed: somebody signed in (or registered), or signed out
    if uid_after and uid_after != uid_before:
        if endpoint == "auth.register":
            events.append(_event("auth.register", uid_after, f"user:{uid_after}", {"email_domain": _domain(form.get("email"))}))
        else:
            events.append(_event("auth.login.ok", uid_after, f"user:{uid_after}", {"via": endpoint}))
    elif uid_before and not uid_after:
        events.append(_event("auth.logout", uid_before, f"user:{uid_before}"))

    # ---- the doors that did not open
    elif endpoint == "auth.login" and method == "POST" and status in (200, 429):
        failure = _event("auth.login.fail", None, "", {"status": 429} if status == 429 else {})
        failure["typed"] = (form.get("name") or "")[:200]  # internal: the hook turns it into an account number or a keyed mark; the text itself is never written
        events.append(failure)
    elif endpoint == "auth.register" and method == "POST" and status in (200, 429):
        events.append(_event("auth.register.fail", None, "", {"name": (form.get("name") or "")[:80], "email_domain": _domain(form.get("email"))}))

    # ---- the confirmation mail and the password reset
    if endpoint == "auth.confirm":
        if method == "POST":
            events.append(_event("auth.confirm.resend", uid_before, f"user:{uid_before}" if uid_before else ""))
        elif view_args.get("data"):
            events.append(_event("auth.confirm.ok" if ok else "auth.confirm.fail", uid_after or uid_before, f"user:{uid_after or uid_before}" if (uid_after or uid_before) else ""))
    elif endpoint == "auth.reset_password" and method == "POST":
        if view_args.get("data"):
            events.append(_event("auth.reset.done" if 300 <= status < 400 else "auth.reset.fail", uid_before, "", {"link": True}))
        else:
            events.append(_event("auth.reset.request", uid_before, "", {"email_domain": _domain(form.get("email"))}))

    # ---- a hint is bought
    elif endpoint == "api.unlocks_unlock_list" and method == "POST" and ok and isinstance(body, dict):
        kind = {"hints": "hint"}.get(body.get("type"), str(body.get("type") or "unlock"))
        events.append(_event("hint.unlock", uid_after or uid_before, f"{kind}:{body.get('target')}"))

    # ---- the player's own account through the API (the settings page records its own change)
    elif endpoint == "api.users_user_private" and method == "PATCH" and ok and isinstance(body, dict):
        fields = sorted(k for k in body if k not in ("confirm", "nonce"))
        if fields:
            events.append(_event("account.update", uid_after or uid_before, f"user:{uid_after or uid_before}", {"fields": fields, "via": "api"}))

    # ---- a challenge instance
    elif endpoint in _INSTANCE_KINDS:
        challenge = (body or {}).get("challenge_id") if endpoint == "l3mon_instances.start_instance" else view_args.get("challenge_id_param")
        base = _INSTANCE_KINDS[endpoint]
        if ok:
            events.append(_event(base, uid_after or uid_before, f"challenge:{challenge}"))
        else:
            events.append(_event(base + ".refused", uid_after or uid_before, f"challenge:{challenge}", {"status": status}))
    return events


# ------------------------------------------------------------------ the hook

def _mark(typed):
    """A short keyed hash of what was typed: the same text gives the same mark (a guessing run shows), but the text cannot be read back, and a weak one cannot be
    guessed back without the platform's secret key."""
    key = current_app.config["SECRET_KEY"]
    key = key if isinstance(key, bytes) else str(key).encode("utf-8")
    return hmac.new(key, ("l3mon-login-fail:" + typed).encode("utf-8"), hashlib.sha256).hexdigest()[:12]


def _failed_sign_in(typed, extra):
    """(target, detail) for a refused sign-in. People type an address, or even their password, into the name box by mistake, so the text typed is NEVER kept: when it
    matches an account the line names the account by number (which one was attacked), otherwise it holds only a keyed mark, what shape it had, and an address's domain."""
    from CTFd.models import Users

    typed = (typed or "").strip()
    by_email = "@" in typed
    column = Users.email if by_email else Users.name
    user = Users.query.filter(func.lower(column) == typed.lower()).first() if typed else None
    if user is not None:
        return f"user:{user.id}", {"account": user.id, "by": "email" if by_email else "name", **extra}
    detail = {"unknown": True, "shape": "email" if by_email else "name", "typed": _mark(typed), **extra}
    if by_email:
        detail["email_domain"] = _domain(typed)
    return "", detail

def _team_of(uid):
    if not uid:
        return None
    try:
        from CTFd.models import Users, db

        return db.session.query(Users.team_id).filter(Users.id == uid).scalar()
    except Exception:
        return None


def _remember():
    g.l3mon_uid_before = session.get("id")


def _write(response):
    try:
        endpoint = request.endpoint
        before, after = getattr(g, "l3mon_uid_before", None), session.get("id")
        interesting = endpoint in _NEEDS_FORM or endpoint in _NEEDS_BODY or endpoint in _INSTANCE_KINDS or endpoint in ("auth.confirm", "auth.logout", "l3mon_theme_sign_out") or before != after
        if not interesting:
            return response
        events = describe(
            endpoint=endpoint, method=request.method, status=response.status_code, view_args=request.view_args or {},
            form={k: request.form.get(k) for k in ("name", "email")} if endpoint in _NEEDS_FORM else {},
            body=request.get_json(silent=True) if endpoint in _NEEDS_BODY else None,
            uid_before=before, uid_after=after,
        )
        for event in events:
            actor = event["actor"]
            if event["kind"] == "auth.login.fail":
                event["target"], event["detail"] = _failed_sign_in(event.pop("typed", ""), event["detail"])
            activity.record(event["kind"], actor=actor, team_id=_team_of(actor), target=event["target"], detail=event["detail"])
    except Exception as err:  # never into the answer the player gets
        _log.error("l3mon: the activity hook failed (%s)", type(err).__name__)
    return response


def install(app) -> None:
    if "l3mon_activity_hooks" in app.extensions:
        return
    app.extensions["l3mon_activity_hooks"] = True
    app.before_request(_remember)
    app.after_request(_write)
