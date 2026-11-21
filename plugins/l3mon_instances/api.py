import datetime
import logging
import os
import re

from flask import Blueprint, request

from CTFd.models import Challenges, db
from CTFd.plugins.l3mon_board import envelope, viewer
from CTFd.plugins.l3mon_core.airing import is_on_air
from CTFd.plugins.l3mon_core.clock import current_phase, now
from CTFd.plugins.l3mon_core.models import Programme
from CTFd.plugins.l3mon_core.visibility import is_visible
from CTFd.plugins.l3mon_instances.client import InstancerClient, InstancerClientError
from CTFd.plugins.l3mon_instances.flag import derive_team_flag, get_flag_secret
from CTFd.plugins.l3mon_instances.hosts import get_host_tracker
from CTFd.plugins.l3mon_instances.models import L3monInstance, L3monInstanceLog
from CTFd.utils.user import get_current_user

_log = logging.getLogger("l3mon.instances.api")
bp = Blueprint("l3mon_instances", __name__, url_prefix="/api/v1/l3mon")

_DIGITS_PATTERN = re.compile(r"^[0-9]{1,9}\Z")
MAX_TEAM_INSTANCES = 3
COOLDOWN_SECONDS = 60


def _parse_id(val) -> int | None:
    if val is None or isinstance(val, bool):
        return None
    if isinstance(val, int):
        s = str(val)
    elif isinstance(val, str):
        s = val
    else:
        return None
    if not _DIGITS_PATTERN.fullmatch(s):
        return None
    return int(s)


def _now_dt() -> datetime.datetime:
    return datetime.datetime.utcfromtimestamp(now())


@bp.route("/instances", methods=["GET"])
@viewer.player
def get_instances():
    team = viewer.current_team()
    if team is None:
        return envelope.fail("no_team", 403, "Set up your studio to carry on.")

    now_dt = _now_dt()
    instances = L3monInstance.query.filter(
        L3monInstance.team_id == team.id,
        L3monInstance.active_key != None,  # noqa: E711
        L3monInstance.expires_at > now_dt,
    ).all()

    tracker = get_host_tracker()
    now_ts = now_dt.timestamp()
    host_answers: dict[str, bool] = {}
    instances_out = []
    freed = False
    for i in instances:
        d = i.to_dict()
        if i.host_name not in host_answers:
            # asked once per host per request; the answer comes from the host-down mark shared by all workers or from the 5-second capacity cache
            host_answers[i.host_name] = tracker.check_host(i.host_name, now_ts=now_ts)
        if not host_answers[i.host_name]:
            d["state"] = "error"
        elif i.state in ("running", "starting") and _gone_from_host(tracker, i, now_dt):
            _free_gone(i)  # the host does not have it any more: it is not listed, and Start makes a new one
            freed = True
            continue
        instances_out.append(d)
    if freed:
        db.session.commit()

    limits = {
        "live": len(instances_out),
        "max": MAX_TEAM_INSTANCES,
        "cooldown_seconds": COOLDOWN_SECONDS,
    }
    return envelope.ok({
        "instances": instances_out,
        "limits": limits,
    })


# A row younger than this is never judged "gone from its host": the host's list may be a few seconds older than the start that made the row
INSTANCE_GRACE_SECONDS = 20


def _gone_from_host(tracker, row, now_dt) -> bool:
    """True when the host says it does not have this instance any more (a crash, the memory limit, a reboot). Never true on a failed question."""
    if (now_dt - row.created_at).total_seconds() < INSTANCE_GRACE_SECONDS:
        return False
    live = tracker.live_instance_ids(row.host_name, now_ts=now_dt.timestamp())
    return live is not None and row.instance_id not in live


def _free_gone(row):
    """The instance no longer exists on its host: free its key, so that the team can start this challenge again. Its state becomes "expired" (the states the
    database allows are starting, running, stopping, expired and error; "expired" is the one for an instance that is no longer live)."""
    row.state = "expired"
    row.active_key = None


def _free_if_expired(row, now_dt):
    """An instance whose time is up (the instancer's reaper removes the container) keeps its unique key until somebody clears it: clear it."""
    if row is not None and row.active_key is not None and row.expires_at <= now_dt:
        if row.state in ("running", "starting"):
            row.state = "expired"
        row.active_key = None
        db.session.commit()
        return True
    return False


@bp.route("/instances", methods=["POST"])
@viewer.player
def start_instance():
    phase = current_phase().state
    if phase != "live":
        return envelope.fail("not_live", 409, f"The contest is {phase}.", phase=phase)

    team = viewer.current_team()
    if team is None:
        return envelope.fail("no_team", 403, "Set up your studio to carry on.")

    user = get_current_user()
    data = request.get_json(silent=True) or {}
    raw_id = data.get("challenge_id")
    challenge_id = _parse_id(raw_id)
    if challenge_id is None:
        return envelope.fail("not_found", 404, "Challenge not found.")

    # Check programme delivery and visibility
    prog = Programme.query.filter_by(challenge_id=challenge_id).first()
    if prog is None or prog.delivery != "live_single":
        return envelope.fail("not_found", 404, "Challenge not found.")

    chal = Challenges.query.get(challenge_id)
    if chal is None or not is_on_air(challenge_id) or not is_visible(chal, solved_ids=viewer.current().solved):
        return envelope.fail("not_found", 404, "Challenge not found.")

    now_dt = _now_dt()
    # 1. Idempotency: if active instance already exists, return it
    active_key = f"{team.id}:{challenge_id}"
    existing = L3monInstance.query.filter_by(active_key=active_key).first()
    if existing:
        if existing.expires_at > now_dt and existing.state in ("running", "starting"):
            tracker = get_host_tracker()
            if not tracker.is_host_down(existing.host_name, now_ts=now_dt.timestamp()) and _gone_from_host(tracker, existing, now_dt):
                # the host does not have this instance any more: do not hand back a dead record, start a new instance
                _free_gone(existing)
                db.session.commit()
            else:
                d = existing.to_dict()
                if tracker.is_host_down(existing.host_name, now_ts=now_dt.timestamp()):
                    d["state"] = "error"
                return envelope.ok(d, status=200)
        # It ran out its time (or ended) and nobody pressed Stop: free its key, or the new row below would collide with it, the new container
        # would be stopped as an orphan and the studio could never start this challenge again
        _free_if_expired(existing, now_dt)

    # 2. Quota check: at most 3 live instances per team
    active_count = L3monInstance.query.filter(
        L3monInstance.team_id == team.id,
        L3monInstance.active_key != None,  # noqa: E711
        L3monInstance.expires_at > now_dt,
    ).count()
    if active_count >= MAX_TEAM_INSTANCES:
        return envelope.fail("quota", 409, f"Studio active instance quota ({MAX_TEAM_INSTANCES}) reached.")

    # 3. Cooldown check: 60s cooldown after previous stop
    last_instance = L3monInstance.query.filter_by(
        team_id=team.id,
        challenge_id=challenge_id,
    ).order_by(L3monInstance.id.desc()).first()
    if last_instance and last_instance.stopped_at:
        elapsed = (now_dt - last_instance.stopped_at).total_seconds()
        if elapsed < COOLDOWN_SECONDS:
            retry_after = max(1, int(COOLDOWN_SECONDS - elapsed))
            return envelope.fail("cooldown", 429, f"Challenge is in cooldown. Retry in {retry_after}s.", retry_after=retry_after)

    # 4. Derive per-team flag
    secret = get_flag_secret()
    if not secret:
        return envelope.fail("busy", 503, "Flag secret not available.")
    flag = derive_team_flag(secret, team.id, challenge_id)

    # 5. Call instancer client with host capacity tracking (retry once on failed start)
    tracker = get_host_tracker()
    now_ts = now_dt.timestamp()
    ttl_seconds = data.get("ttl_seconds") if os.environ.get("L3MON_INSTANCES_TTL_OVERRIDE") == "1" else None

    chosen_host = None
    client = None
    inst_data = None
    busy_hosts: set[str] = set()  # hosts that answered "busy" during THIS request: alive, passed over for now, never marked down
    busy_retry_after = None

    for attempt in range(2):
        try:
            chosen_host = tracker.select_host_for_start(slug=prog.slug, now_ts=now_ts, exclude=busy_hosts)
        except InstancerClientError as e:
            if e.error_code == "no_capacity":
                return envelope.fail("no_capacity", 503, e.message, retry_after=busy_retry_after or e.retry_after or 30)
            return envelope.fail("busy", 503, e.message)

        client = tracker.get_client_for_host(chosen_host["name"])
        try:
            inst_data = client.start_instance(
                team_id=team.id,
                challenge_id=challenge_id,
                slug=prog.slug,
                flag=flag,
                ttl_seconds=ttl_seconds,
            )
            break
        except InstancerClientError as e:
            # Client errors (cooldown, quota, not_found) are not host failures
            if e.error_code in ("cooldown", "quota", "not_found"):
                if e.error_code == "cooldown":
                    return envelope.fail("cooldown", 429, e.message, retry_after=e.retry_after or 60)
                elif e.error_code == "quota":
                    return envelope.fail("quota", 409, e.message)
                elif e.error_code == "not_found":
                    return envelope.fail("not_found", 404, e.message)

            # A start that failed INSIDE the instancer (an image that cannot be pulled, a container that dies) is this challenge's failure, not the host's:
            # marking the host down would refuse every other studio's starts and show 'error' on their running instances for 30 s. Retrying would only
            # start the same container again. The player gets a plain message (the instancer's own text names images and addresses).
            if e.error_code in ("internal_error", "invalid_request"):
                _log.error("Start instance failed inside the instancer on host %s for challenge %s: %s", chosen_host["name"], challenge_id, e)
                return envelope.fail("start_failed", 503, "This challenge could not be started just now. Try again in a minute; if it keeps failing, tell the crew.")

            # The host answered "busy" (it is working through a burst of starts, or is at its memory ceiling): it is ALIVE. Pass it over for this request and
            # try another host; never mark it down (found on the real host, 21 Nov 2026: that turned a slow minute into a 30 second outage for everybody).
            if e.error_code == "no_capacity":
                busy_hosts.add(chosen_host["name"])
                busy_retry_after = e.retry_after
                if attempt == 0:
                    continue
                return envelope.fail("no_capacity", 503, e.message, retry_after=e.retry_after or 30)

            # The host did not answer in time: it is SLOW, not dead, and the instance may have been created all the same. Ask the host what it has for this team
            # before anything else is done: a second start would be a second instance. Never mark the host down for a slow answer.
            if e.error_code == "timeout":
                _log.warning("Start instance timed out on host %s for challenge %s; asking the host whether it landed", chosen_host["name"], challenge_id)
                inst_data = _find_started_instance(client, team.id, challenge_id)
                if inst_data:
                    break
                return envelope.fail("busy", 503, "The challenge servers are busy right now. Try again in a minute; if it keeps failing, tell the crew.")

            # Host failure (connection error, 5xx server error, or capacity issue)
            _log.warning("Start instance failed on host %s: %s (attempt %d)", chosen_host["name"], e, attempt + 1)
            tracker.mark_host_down(chosen_host["name"], now_ts=now_ts)
            if attempt == 0:
                continue
            else:
                if e.error_code == "no_capacity":
                    return envelope.fail("no_capacity", 503, e.message, retry_after=e.retry_after or 30)
                return envelope.fail("busy", 503, "The challenge servers are busy. Try again in a minute; if it keeps failing, tell the crew.")

    # 6. Record in DB
    created_ts = inst_data.get("created_at") or int(now_dt.timestamp())
    expires_ts = inst_data.get("expires_at") or int((now_dt + datetime.timedelta(minutes=45)).timestamp())
    created_at = datetime.datetime.utcfromtimestamp(created_ts)
    expires_at = datetime.datetime.utcfromtimestamp(expires_ts)

    # For TCP instances, host comes from chosen host's public TCP host if provided
    host_val = inst_data.get("host")
    if inst_data.get("protocol") == "tcp":
        host_val = chosen_host.get("public_tcp_host") or chosen_host.get("host") or host_val

    # Query log record BEFORE adding instance_row so autoflush does not trigger a premature insert
    log_row = L3monInstanceLog.query.filter_by(team_id=team.id, challenge_id=challenge_id).first()

    instance_row = L3monInstance(
        instance_id=inst_data["id"],
        team_id=team.id,
        challenge_id=challenge_id,
        slug=prog.slug,
        protocol=inst_data.get("protocol", "http"),
        state="running",
        host_name=chosen_host["name"],
        url=inst_data.get("url"),
        host=host_val,
        port=inst_data.get("port"),
        created_at=created_at,
        expires_at=expires_at,
        active_key=active_key,
    )
    db.session.add(instance_row)
    if not log_row:
        db.session.add(L3monInstanceLog(team_id=team.id, challenge_id=challenge_id, first_seen_at=now_dt))

    try:
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        _log.warning("Database insert race for instance active_key: %s", e)
        # Return the winning existing instance, stopping any orphaned instance on a losing host
        existing = L3monInstance.query.filter_by(active_key=active_key).first()
        if existing:
            # Stop only an instance that is NOT the one now recorded in the DB for that team/challenge
            # (a different id or a different host). On a single host where start_instance returns the
            # existing instance, existing.instance_id == inst_data["id"], so we do NOT stop it!
            if existing.instance_id != inst_data.get("id") or existing.host_name != chosen_host["name"]:
                try:
                    client.stop_instance(inst_data["id"])
                except Exception as stop_err:
                    _log.warning("Failed to stop orphaned instance %s on host %s: %s", inst_data["id"], chosen_host["name"], stop_err)

            d = existing.to_dict()
            if tracker.is_host_down(existing.host_name, now_ts=now_dt.timestamp()):
                d["state"] = "error"
            return envelope.ok(d, status=200)

        # Fallback if existing record not found: stop the created instance
        try:
            client.stop_instance(inst_data["id"])
        except Exception as stop_err:
            _log.warning("Failed to stop orphaned instance %s on host %s: %s", inst_data["id"], chosen_host["name"], stop_err)
        return envelope.fail("busy", 503, "Could not register instance.")

    return envelope.ok(instance_row.to_dict(), status=201)


def _find_started_instance(client, team_id: int, challenge_id: int):
    """The instance a host has for this team and challenge, if a start that timed out landed all the same; None if it did not, or the host cannot say."""
    try:
        for inst in client.list_instances(team_id=team_id):
            if inst.get("challenge_id") == challenge_id and inst.get("state") in ("running", "starting") and inst.get("id"):
                return inst
    except Exception as e:
        _log.warning("Could not ask the host whether a timed-out start landed: %s", e)
    return None


@bp.route("/instances/<challenge_id_param>/renew", methods=["POST"])
@viewer.player
def renew_instance(challenge_id_param):
    phase = current_phase().state
    if phase != "live":
        return envelope.fail("not_live", 409, f"The contest is {phase}.", phase=phase)

    challenge_id = _parse_id(challenge_id_param)
    if challenge_id is None:
        return envelope.fail("not_found", 404, "Challenge not found.")

    team = viewer.current_team()
    if team is None:
        return envelope.fail("no_team", 403, "Set up your studio to carry on.")

    prog = Programme.query.filter_by(challenge_id=challenge_id).first()
    if prog is None or prog.delivery != "live_single":
        return envelope.fail("not_found", 404, "Challenge not found.")

    chal = Challenges.query.get(challenge_id)
    if chal is None or not is_on_air(challenge_id) or not is_visible(chal, solved_ids=viewer.current().solved):
        return envelope.fail("not_found", 404, "Challenge not found.")

    active_key = f"{team.id}:{challenge_id}"
    instance_row = L3monInstance.query.filter_by(active_key=active_key).first()
    if instance_row is not None and _free_if_expired(instance_row, _now_dt()):
        instance_row = None  # its time was up: there is nothing left to renew
    if not instance_row:
        return envelope.fail("not_found", 404, "Instance not found.")

    tracker = get_host_tracker()
    try:
        client = tracker.get_client_for_host(instance_row.host_name)
    except ValueError as e:
        _log.error("Cannot renew instance %s: %s", instance_row.instance_id, e)
        return envelope.fail("busy", 503, "The challenge host of this instance is not set up correctly. Tell the crew.")
    try:
        renewed_data = client.renew_instance(instance_row.instance_id)
    except InstancerClientError as e:
        extra = {}
        if e.error_code == "cooldown":
            extra["retry_after"] = e.retry_after or 60
        elif e.retry_after is not None:
            extra["retry_after"] = e.retry_after
        return envelope.fail(e.error_code, e.status, e.message, **extra)

    expires_ts = renewed_data.get("expires_at")
    if expires_ts:
        instance_row.expires_at = datetime.datetime.utcfromtimestamp(expires_ts)
    instance_row.renewals = renewed_data.get("renewals", instance_row.renewals + 1)
    instance_row.last_renewed_at = _now_dt()
    db.session.commit()

    return envelope.ok(instance_row.to_dict(), status=200)


@bp.route("/instances/<challenge_id_param>", methods=["DELETE"])
@viewer.player
def stop_instance(challenge_id_param):
    challenge_id = _parse_id(challenge_id_param)
    if challenge_id is None:
        return envelope.fail("not_found", 404, "Challenge not found.")

    team = viewer.current_team()
    if team is None:
        return envelope.fail("no_team", 403, "Set up your studio to carry on.")

    active_key = f"{team.id}:{challenge_id}"
    instance_row = L3monInstance.query.filter_by(active_key=active_key).first()
    if not instance_row:
        # Idempotent stop: return success if already stopped
        return envelope.ok({"message": "Stopped."}, status=200)

    tracker = get_host_tracker()
    try:
        client = tracker.get_client_for_host(instance_row.host_name)
        client.stop_instance(instance_row.instance_id)
    except Exception as e:  # includes a host without its token (ValueError): the host's own reaper removes the instance when its time is up
        _log.warning("Client error stopping instance %s: %s", instance_row.instance_id, e)

    instance_row.state = "stopping"
    instance_row.stopped_at = _now_dt()
    instance_row.active_key = None
    db.session.commit()

    return envelope.ok({"message": "Stopped."}, status=200)
