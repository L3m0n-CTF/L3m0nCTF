import datetime
import logging
import re
from flask import Blueprint, request

from CTFd.models import db
from CTFd.plugins.l3mon_board import envelope
from CTFd.plugins.l3mon_core import audit
from CTFd.plugins.l3mon_core.clock import now
from CTFd.plugins.l3mon_instances.client import InstancerClient
from CTFd.plugins.l3mon_instances.hosts import get_host_tracker
from CTFd.plugins.l3mon_instances.models import L3monInstance
from CTFd.utils.decorators import admins_only

_log = logging.getLogger("l3mon.instances.admin")
admin_bp = Blueprint("l3mon_instances_admin", __name__, url_prefix="/api/v1/l3mon/admin/instances")

_DIGITS_PATTERN = re.compile(r"^[0-9]{1,9}\Z")


def _now_dt() -> datetime.datetime:
    return datetime.datetime.utcfromtimestamp(now())


def _parse_id(val) -> int | None:
    if val is None:
        return None
    s = str(val).strip()
    if not _DIGITS_PATTERN.fullmatch(s):
        return None
    return int(s)


@admin_bp.route("", methods=["GET"])
@admins_only
def list_instances():
    raw_team = request.args.get("team_id")
    raw_chal = request.args.get("challenge_id")

    q = L3monInstance.query.filter(
        L3monInstance.active_key != None,  # noqa: E711
        L3monInstance.expires_at > _now_dt(),
    )
    if raw_team is not None:
        tid = _parse_id(raw_team)
        if tid is None:
            return envelope.fail("invalid_param", 400, "Invalid team id.")
        q = q.filter(L3monInstance.team_id == tid)

    if raw_chal is not None:
        cid = _parse_id(raw_chal)
        if cid is None:
            return envelope.fail("invalid_param", 400, "Invalid challenge id.")
        q = q.filter(L3monInstance.challenge_id == cid)

    tracker = get_host_tracker()
    now_ts = _now_dt().timestamp()
    instances = q.all()
    results = []
    for inst in instances:
        d = inst.to_dict()
        d["id"] = inst.instance_id
        d["team_id"] = inst.team_id
        d["slug"] = inst.slug
        d["host_name"] = inst.host_name
        if tracker.is_host_down(inst.host_name, now_ts=now_ts):
            d["state"] = "error"
        results.append(d)

    return envelope.ok({"instances": results})


@admin_bp.route("/kill", methods=["POST"])
@admins_only
def kill_instances():
    # "Stop everything" must be asked for: an empty object {}. A body that is missing, cut off, not an object, or that names a field we do not know (a wrong key
    # such as {"team": 13}) used to be read as {} and stopped every instance of every studio (found by the end-to-end test, 2026-11-15).
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return envelope.fail("invalid_body", 400, 'Send a JSON object: {} stops every instance, {"team_id": 3} one studio, {"challenge_id": 7} one challenge.')
    unknown = sorted(str(key) for key in data if key not in ("team_id", "challenge_id"))
    if unknown:
        return envelope.fail("invalid_param", 400, f"Unknown field(s): {', '.join(unknown)}. Nothing was stopped. Use team_id and/or challenge_id, or {{}} for everything.")
    raw_team = data.get("team_id")
    raw_chal = data.get("challenge_id")

    team_id = None
    if raw_team is not None:
        team_id = _parse_id(raw_team)
        if team_id is None:
            return envelope.fail("invalid_param", 400, "Invalid team id.")

    challenge_id = None
    if raw_chal is not None:
        challenge_id = _parse_id(raw_chal)
        if challenge_id is None:
            return envelope.fail("invalid_param", 400, "Invalid challenge id.")

    tracker = get_host_tracker()
    stopped = 0
    for host in tracker.get_hosts():
        try:
            client = tracker.get_client_for_host(host["name"])
            stopped += client.kill_instances(team_id=team_id, challenge_id=challenge_id)
        except Exception as e:
            _log.warning("Instancer client error during kill_instances on host %s: %s", host.get("name"), e)

    # Mark local database rows
    now_dt = _now_dt()
    q = L3monInstance.query.filter(L3monInstance.active_key != None)  # noqa: E711
    if team_id is not None:
        q = q.filter(L3monInstance.team_id == team_id)
    if challenge_id is not None:
        q = q.filter(L3monInstance.challenge_id == challenge_id)

    db_updated = q.update(
        {
            L3monInstance.state: "stopping",
            L3monInstance.stopped_at: now_dt,
            L3monInstance.active_key: None,
        },
        synchronize_session=False,
    )
    if stopped == 0 and db_updated > 0:
        stopped = db_updated

    # Audit log
    audit.record(
        action="instances.kill",
        target=f"team:{team_id or '*'}/chal:{challenge_id or '*'}",
        detail=f"Stopped {stopped} instance(s)",
    )
    db.session.commit()

    return envelope.ok({"stopped": stopped})
