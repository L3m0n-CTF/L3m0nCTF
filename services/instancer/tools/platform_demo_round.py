# The platform-driven demo round of the instancer (22 Nov 2026), run INSIDE the platform's application container, on the platform VM:
#
#   bash services/instancer/tools/run_platform_demo.sh <command>          (on the VM; from your laptop use tools/ops/demo-round.sh, which ships and runs it)
#
# It goes through the platform's own plan, release and instance code and through the real challenge host; nothing is mocked. Everything it makes is a hidden
# test user and studio (named test-inst-NN) or a programme in the channel "crew-test", and the cleanup command takes all of it away again. It prints no secret: test accounts
# get random passwords that are never shown, and a session is made with CTFd's own login_user. The "New on air" announcement is switched off for the run (this
# process only), so a crew test never reaches the players' bell. Read docs/runbooks/19-instancer-demo-round.md before using it on production.
#
# Commands: setup_programme | setup | probe | counts | functional | burst | hostdown_during | hostdown_after | gone | after_gone | reset | expected_tcp | cleanup
import json
import sys
import time

from CTFd import create_app
from CTFd.models import Challenges, Flags, Solves, Submissions, Teams, Users, db

app = create_app()
CMD = sys.argv[1] if len(sys.argv) > 1 else "probe"
HOST = "https://play.l3m0nctf.xyz"
CHANNEL = "crew-test"
DEMOS = [  # (slug on the challenge host, name, number)
    ("demo-web", "Demo web", 901),
    ("demo-web2", "Demo web two", 902),
    ("demo-web3", "Demo web three", 903),
    ("demo-web-short", "Demo web short", 904),
    ("demo-tcp", "Demo tcp", 905),
]
PREFIX = "test-inst"


def out(*a):
    print(*a, flush=True)


def make_studio(i):
    import secrets
    name = f"{PREFIX}-{i:02d}"
    u = Users.query.filter_by(name=name).first()
    if u is not None:
        return u.id, u.team_id
    u = Users(name=name, email=f"{name}@example.invalid", password=secrets.token_urlsafe(24), verified=True, hidden=True)
    db.session.add(u)
    db.session.flush()
    t = Teams(name=f"{PREFIX}-studio-{i:02d}", password=secrets.token_urlsafe(24), captain_id=u.id, hidden=True)
    db.session.add(t)
    db.session.flush()
    u.team_id = t.id
    db.session.commit()
    return u.id, t.id


def client_for(user_id, i=1):
    from flask import session
    from CTFd.utils.security.auth import login_user
    user = Users.query.get(user_id)
    with app.test_request_context("/", base_url=HOST):
        login_user(user)
        data = dict(session)
    c = app.test_client()
    c.environ_base["REMOTE_ADDR"] = f"10.99.{i % 250}.1"
    with c.session_transaction(base_url=HOST) as s:
        s.update(data)
    return c, data.get("nonce")


def call(c, nonce, method, path, body=None):
    kw = {"base_url": HOST, "headers": {"CSRF-Token": nonce or ""}}
    if body is None and method in ("post", "put", "delete", "patch"):
        body = {}  # CTFd checks the CSRF header only for a JSON request: an empty body is sent as {}
    if body is not None:
        kw["json"] = body
    r = getattr(c, method)(path, **kw)
    try:
        return r.status_code, r.get_json(silent=True)
    except Exception:
        return r.status_code, None


def setup(with_studios=True):
    from CTFd.plugins.l3mon_core import audit
    from CTFd.plugins.l3mon_core.clock import now
    from CTFd.plugins.l3mon_core.models import Channel, Programme
    from CTFd.plugins.l3mon_release import plan
    from CTFd.plugins.l3mon_release.reconcile import reconcile, serialize
    import CTFd.plugins.l3mon_release.reconcile as rc
    from CTFd.models import Notifications
    # a crew test must not tell players "New on air": this run (only this process) does not announce
    rc.announce = lambda *a, **k: None
    notes_before = Notifications.query.count()
    ids = []
    for slug, name, number in DEMOS:
        row = Challenges.query.filter_by(name=name).first()
        if row is None:
            row = Challenges(name=name, category="crew-test", description="Crew test of the instancer. Not part of the contest.", value=0, state="hidden", type="standard", max_attempts=0)
            db.session.add(row)
            db.session.flush()
            db.session.add(Flags(challenge_id=row.id, type="l3mon_dynamic", content=""))
        ids.append((row.id, slug, number))
    db.session.commit()
    body = {
        "channels": [{"slug": CHANNEL, "name": "Crew test", "position": 98}],
        "programmes": [{"challenge_id": cid, "channel": CHANNEL, "cell": n - 901, "number": n, "slug": slug, "delivery": "live_single"} for cid, slug, n in ids],
    }
    serialize()
    clean, problems = plan.validate_plan(body)
    if problems:
        out("PLAN REFUSED:", dict(problems))
        return 1
    counts = plan.apply_plan(clean)
    audit.record("plan.sync", "plan", "demo round of the instancer (crew test channel)")
    reconcile(locked=True)
    out("plan applied:", json.dumps(counts))
    serialize()
    t = now()
    ch = Channel.query.filter_by(slug=CHANNEL).one()
    changes = [{"kind": "channel", "id": ch.id, "mode": "release"}] + [{"kind": "programme", "id": p.id, "mode": "release"} for p in Programme.query.filter_by(channel_id=ch.id).all()]
    clean, problems = plan.validate_changes({"changes": changes}, t)
    if problems:
        out("RELEASE REFUSED:", dict(problems))
        return 1
    plan.apply_changes(clean)
    reconcile(t, locked=True)
    out("released: channel", ch.slug, "and", len(changes) - 1, "programmes")
    out("notifications before / after (must be equal):", notes_before, Notifications.query.count())
    if with_studios:
        for i in (1, 2):
            uid, tid = make_studio(i)
            out(f"studio {i}: user {uid}, team {tid}")
    return 0


def probe():
    uid, tid = make_studio(1)
    c, nonce = client_for(uid)
    st, body = call(c, nonce, "get", "/api/v1/l3mon/instances")
    out("GET instances as test studio 1:", st, json.dumps(body)[:300])
    st, body = call(c, nonce, "get", "/api/v1/l3mon/board")
    out("GET board as test studio 1:", st, json.dumps(body)[:400])
    return 0



RESULTS = {"pass": 0, "fail": 0}


def check(name, ok, detail=""):
    RESULTS["pass" if ok else "fail"] += 1
    out(("PASS " if ok else "FAIL ") + name + (("  --  " + str(detail)[:230]) if detail != "" else ""))
    return ok


def fetch(url, path="/flag", wait=20.0):
    import urllib.error
    import urllib.request
    t0 = time.time()
    last = None
    while time.time() - t0 < wait:
        try:
            req = urllib.request.Request(url.rstrip("/") + path, headers={"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36"})
            with urllib.request.urlopen(req, timeout=8) as r:
                return r.status, r.read().decode(errors="replace").strip(), round(time.time() - t0, 1)
        except urllib.error.HTTPError as e:
            last = e.code
        except Exception as e:  # noqa: BLE001
            last = type(e).__name__
        time.sleep(0.4)
    return last, "", round(time.time() - t0, 1)


def expected_flag(team_id, challenge_id):
    from CTFd.plugins.l3mon_instances.flag import derive_team_flag, get_flag_secret
    return derive_team_flag(get_flag_secret(), team_id, challenge_id)


def demo_ids():
    return {slug: Challenges.query.filter_by(name=name).one().id for slug, name, _ in DEMOS}


def inst(body):
    d = (body or {}).get("data") or {}
    return d.get("instance", d)


def functional():
    ids = demo_ids()
    ua, ta = make_studio(1)
    ub, tb = make_studio(2)
    ca, na = client_for(ua, 1)
    cb, nb = client_for(ub, 2)
    # 1. a start through the platform, the way the page does it
    t0 = time.time()
    st, body = call(ca, na, "post", "/api/v1/l3mon/instances", {"challenge_id": ids["demo-web"]})
    took = round(time.time() - t0, 1)
    a_web = inst(body)
    check("A starts demo-web through the platform (201)", st == 201 and a_web.get("url"), f"{st} in {took}s, keys {sorted(a_web)[:8]}")
    check("the address handed to the player is https", str(a_web.get("url", "")).startswith("https://"), a_web.get("url"))
    out("URL_A_WEB", a_web.get("url"))
    st2, body2 = call(ca, na, "post", "/api/v1/l3mon/instances", {"challenge_id": ids["demo-web"]})
    check("the same start again is idempotent (200, same address)", st2 == 200 and inst(body2).get("url") == a_web.get("url"), f"{st2}")
    st, body = call(cb, nb, "post", "/api/v1/l3mon/instances", {"challenge_id": ids["demo-web"]})
    b_web = inst(body)
    check("B starts demo-web (201) at a different address", st == 201 and b_web.get("url") and b_web["url"] != a_web.get("url"), f"{st}")
    out("URL_B_WEB", b_web.get("url"))
    st, body = call(cb, nb, "post", "/api/v1/l3mon/instances", {"challenge_id": ids["demo-web-short"]})
    t_short = time.time()
    b_short = inst(body)
    check("B starts demo-web-short (a 1 minute life)", st == 201 and b_short.get("url"), f"{st} {b_short.get('expires_in')}s")
    # 2. through Cloudflare, from this machine
    for tag, who, team_id, chal, row in (("A", "A", ta, ids["demo-web"], a_web), ("B", "B", tb, ids["demo-web"], b_web)):
        code, served, waited = fetch(row.get("url", ""), "/flag", 25)
        want = expected_flag(team_id, chal)
        check(f"{tag}: the address answers through Cloudflare and serves THIS studio's flag (the platform's own derivation)", code == 200 and served == want, f"http {code} after {waited}s, flag matches {served == want}")
    code, health, _ = fetch(a_web.get("url", ""), "/health", 5)
    check("A: /health answers 200", code == 200, code)
    # 3. solving through the platform: A's flag is right for A and wrong for B
    fa = expected_flag(ta, ids["demo-web"])
    st, body = call(ca, na, "post", "/api/v1/challenges/attempt", {"challenge_id": ids["demo-web"], "submission": fa})
    check("A submits its own flag: accepted", st == 200 and "correct" in json.dumps(body).lower() and "incorrect" not in json.dumps(body).lower(), f"{st} {json.dumps(body)[:160]}")
    st, body = call(cb, nb, "post", "/api/v1/challenges/attempt", {"challenge_id": ids["demo-web"], "submission": fa})
    check("B submits A's flag: refused", st in (200, 400, 429) and "incorrect" in json.dumps(body).lower(), f"{st} {json.dumps(body)[:160]}")
    # 4. renewal is off: an instance lives 15 minutes and cannot be extended (owner's rule of 23 Nov 2026), so the platform answers 409 renew_limit
    st, body = call(ca, na, "post", f"/api/v1/l3mon/instances/{ids['demo-web']}/renew")
    check("A's renew is refused (409 renew_limit): an instance cannot be extended", st == 409 and "renew_limit" in json.dumps(body), f"{st} {json.dumps(body)[:140]}")
    check("the refusal says what to do (start a new one)", "new one" in json.dumps(body).lower(), json.dumps(body)[:140])
    check("an instance's life, as the start answer said, is at most 15 minutes", 0 < (a_web.get("expires_in") or 10**9) <= 15 * 60 + 5, f"{a_web.get('expires_in')} s")
    # 5. the quota of three
    for slug in ("demo-web2", "demo-web3"):
        st, body = call(ca, na, "post", "/api/v1/l3mon/instances", {"challenge_id": ids[slug]})
        check(f"A starts {slug} (201)", st == 201, st)
    st, body = call(ca, na, "post", "/api/v1/l3mon/instances", {"challenge_id": ids["demo-web-short"]})
    check("A's 4th live instance is refused (409 quota)", st == 409 and "quota" in json.dumps(body), f"{st} {json.dumps(body)[:120]}")
    st, body = call(ca, na, "get", "/api/v1/l3mon/instances")
    check("A's list shows the three live instances", st == 200 and len(body["data"]["instances"]) == 3, f"{st} {len((body or {}).get('data', {}).get('instances', []))}")
    # 6. a raw TCP instance
    st, body = call(ca, na, "delete", f"/api/v1/l3mon/instances/{ids['demo-web3']}")
    check("A stops demo-web3 (200)", st == 200, st)
    st, body = call(ca, na, "post", "/api/v1/l3mon/instances", {"challenge_id": ids["demo-tcp"]})
    a_tcp = inst(body)
    check("A starts demo-tcp (201) and is given the host's TCP address and a port in 30000-30199", st == 201 and a_tcp.get("port") and 30000 <= a_tcp["port"] <= 30199 and a_tcp.get("host"), f"{st} {a_tcp.get('host')}:{a_tcp.get('port')}")
    out("TCP_A", a_tcp.get("host"), a_tcp.get("port"), "private address check from the challenge VM follows")
    # 7. stop: the address goes dark, and the cooldown holds a restart back
    st, body = call(ca, na, "delete", f"/api/v1/l3mon/instances/{ids['demo-web']}")
    check("A stops demo-web (200)", st == 200, st)
    t1 = time.time()
    code = None
    while time.time() - t1 < 15:
        code, _, _ = fetch(a_web.get("url", ""), "/health", 0.5)
        if code == 404:
            break
        time.sleep(0.3)
    check("after the stop, A's address answers 404 within a few seconds", code == 404, f"{code} after {round(time.time() - t1, 1)}s")
    st, body = call(ca, na, "post", "/api/v1/l3mon/instances", {"challenge_id": ids["demo-web"]})
    check("an immediate restart is held back by the cooldown (429)", st == 429, f"{st} {json.dumps(body)[:100]}")
    # 8. B's address is untouched by A's stop
    code, served, _ = fetch(b_web.get("url", ""), "/flag", 5)
    check("B's instance still serves B's flag after A stopped", code == 200 and served == expected_flag(tb, ids["demo-web"]), code)
    # 9. expiry of B's 1 minute instance, seen by the platform
    wait = 80 - (time.time() - t_short)
    if wait > 0:
        out(f"waiting {wait:.0f}s for the 1 minute instance to expire ...")
        time.sleep(wait)
    code, _, _ = fetch(b_short.get("url", ""), "/health", 12)
    st, body = call(cb, nb, "get", "/api/v1/l3mon/instances")
    left = [i for i in (body or {}).get("data", {}).get("instances", []) if i.get("challenge_id") == ids["demo-web-short"] and i.get("state") == "running"]
    check("B's 1 minute instance has expired: its address is 404 and the platform no longer lists it as running", code == 404 and not left, f"http {code}, still listed: {len(left)}")
    out(f"RESULT: {RESULTS['pass']} passed, {RESULTS['fail']} failed")
    return 1 if RESULTS["fail"] else 0


def reset():
    """Stop every instance of the test studios the way the page does it (so the platform and the host agree again)."""
    ids = demo_ids()
    users = Users.query.filter(Users.name.like(PREFIX + "-%")).order_by(Users.id).all()
    stopped = 0
    for k, u in enumerate(users):
        c, nonce = client_for(u.id, k + 1)
        for cid in ids.values():
            st, _ = call(c, nonce, "delete", f"/api/v1/l3mon/instances/{cid}")
            stopped += 1 if st == 200 else 0
    team_ids = [u.team_id for u in users if u.team_id]
    if team_ids:  # the earlier run's solves of the hidden test studios go, so that a flag can be submitted again
        Solves.query.filter(Solves.team_id.in_(team_ids)).delete(synchronize_session=False)
        Submissions.query.filter(Submissions.team_id.in_(team_ids)).delete(synchronize_session=False)
        db.session.commit()
    out(f"reset: {stopped} stop calls answered 200 for {len(users)} test studios; their old solves and submissions removed")
    return 0


def burst():
    import threading
    n = 24
    cid = demo_ids()["demo-web"]
    first = 3
    studios = [make_studio(first + k) for k in range(n)]
    clients = [client_for(u, first + k) for k, (u, _) in enumerate(studios)]
    results = [None] * n
    barrier = threading.Barrier(n)

    def work(k):
        with app.app_context():
            c, nonce = clients[k]
            barrier.wait()
            t0 = time.time()
            st, body = call(c, nonce, "post", "/api/v1/l3mon/instances", {"challenge_id": cid})
            results[k] = (st, round(time.time() - t0, 1), inst(body), body)

    threads = [threading.Thread(target=work, args=(k,)) for k in range(n)]
    t0 = time.time()
    for t in threads:
        t.start()
    for t in threads:
        t.join(120)
    total = round(time.time() - t0, 1)
    sts = [r[0] if r else None for r in results]
    lat = sorted(r[1] for r in results if r)
    check(f"{n} studios start at the same moment: all {n} answer 201", sts.count(201) == n, f"statuses {sorted(set(map(str, sts)))}, counts {[(x, sts.count(x)) for x in set(sts)]}")
    check("every studio got its own address, all https", len({r[2].get('url') for r in results if r}) == n and all(str(r[2].get('url', '')).startswith("https://") for r in results if r), "")
    out(f"BURST: {n} starts in {total}s overall; per-request seconds: min {lat[0]}, median {lat[len(lat)//2]}, p95 {lat[int(len(lat)*0.95)-1]}, max {lat[-1]}")
    bad = [(k, r[0], str(r[3])[:140]) for k, r in enumerate(results) if r and r[0] != 201]
    if bad:
        out("not 201:", bad[:6])
    ok = 0
    mism = 0
    for k, r in enumerate(results):
        if not r or r[0] != 201:
            continue
        code, served, _ = fetch(r[2].get("url", ""), "/flag", 25)
        if code == 200 and served == expected_flag(studios[k][1], cid):
            ok += 1
        else:
            mism += 1
            out(f"studio {first + k}: http {code}, flag matches {served == expected_flag(studios[k][1], cid)}")
    check(f"every one of the {n} addresses answers through Cloudflare and serves its own studio's flag", ok == n, f"{ok} of {n} correct, {mism} wrong")
    # stop them all the way the page does
    stopped = 0
    for k, (c, nonce) in enumerate(clients):
        st, _ = call(c, nonce, "delete", f"/api/v1/l3mon/instances/{cid}")
        stopped += 1 if st == 200 else 0
    check(f"all {n} stops answered 200", stopped == n, stopped)
    out(f"RESULT: {RESULTS['pass']} passed, {RESULTS['fail']} failed")
    return 1 if RESULTS["fail"] else 0


def hostdown_during():
    """The challenge host's agent is stopped (done by hand). A start by a studio with no instance must answer quickly and plainly; an instance on it is shown as an error."""
    ids = demo_ids()
    u3, t3 = make_studio(3)
    u2, t2 = make_studio(2)
    c3, n3 = client_for(u3, 3)
    c2, n2 = client_for(u2, 2)
    t0 = time.time()
    st, body = call(c3, n3, "post", "/api/v1/l3mon/instances", {"challenge_id": ids["demo-web"]})
    took = round(time.time() - t0, 1)
    check("host down: a start by a studio with no instance is refused plainly (503) and without a long wait", st == 503 and took < 20, f"{st} in {took}s {json.dumps(body)[:160]}")
    st, body = call(c2, n2, "get", "/api/v1/l3mon/instances")
    states = [(i.get("challenge_id"), i.get("state")) for i in (body or {}).get("data", {}).get("instances", [])]
    check("host down: a studio's running instance is shown as an error, not as running", st == 200 and states and all(x[1] == "error" for x in states), f"{st} {states}")
    return 1 if RESULTS["fail"] else 0


def hostdown_after():
    """The agent is back. Within the platform's down-mark time (30 s) a start works again, and the earlier instance is shown as running again."""
    ids = demo_ids()
    u3, t3 = make_studio(3)
    u2, t2 = make_studio(2)
    c3, n3 = client_for(u3, 3)
    c2, n2 = client_for(u2, 2)
    t0 = time.time()
    st = None
    while time.time() - t0 < 70:
        st, body = call(c3, n3, "post", "/api/v1/l3mon/instances", {"challenge_id": ids["demo-web"]})
        if st in (200, 201):
            break
        time.sleep(3)
    check("host back: a fresh start works again", st == 201, f"{st} after {round(time.time() - t0, 1)}s")
    states = None
    t1 = time.time()
    while time.time() - t1 < 70:
        st2, body2 = call(c2, n2, "get", "/api/v1/l3mon/instances")
        states = [(i.get("challenge_id"), i.get("state")) for i in (body2 or {}).get("data", {}).get("instances", [])]
        if states and all(x[1] == "running" for x in states):
            break
        time.sleep(3)
    check("host back: the studio's earlier instance is shown as running again (it survived the agent's stop)", bool(states) and all(x[1] == "running" for x in states), f"after {round(time.time() - t1, 1)}s {states}")
    return 1 if RESULTS["fail"] else 0


def gone():
    """An instance that disappears from the host (removed by hand there) is freed on the platform within the grace period. Reads and prints only."""
    from CTFd.plugins.l3mon_instances.models import L3monInstance
    u2, t2 = make_studio(2)
    rows = L3monInstance.query.filter_by(team_id=t2).filter(L3monInstance.state.in_(["running", "starting"])).all()
    for r in rows:
        out("INSTANCE_ID", r.instance_id, "challenge", r.challenge_id, "state", r.state)
    return 0


def after_gone():
    u2, t2 = make_studio(2)
    c2, n2 = client_for(u2, 2)
    t0 = time.time()
    left = None
    while time.time() - t0 < 45:
        st, body = call(c2, n2, "get", "/api/v1/l3mon/instances")
        left = [i for i in (body or {}).get("data", {}).get("instances", []) if i.get("state") == "running"]
        if not left:
            break
        time.sleep(3)
    check("gone from the host: the platform stops showing it as running (grace 20 s)", not left, f"after {round(time.time() - t0, 1)}s, still shown: {len(left or [])}")
    return 1 if RESULTS["fail"] else 0


def cleanup():
    import CTFd.plugins.l3mon_release.reconcile as rc
    rc.announce = lambda *a, **k: None
    from CTFd.models import Awards, Notifications
    from CTFd.plugins.l3mon_core import audit
    from CTFd.plugins.l3mon_core.models import Channel, Programme
    from CTFd.plugins.l3mon_instances.hosts import get_host_tracker
    from CTFd.plugins.l3mon_instances.models import L3monInstance, L3monInstanceLog
    from CTFd.plugins.l3mon_release.reconcile import reconcile, serialize
    from sqlalchemy import or_
    users = Users.query.filter(Users.name.like(PREFIX + "-%")).all()
    team_ids = [u.team_id for u in users if u.team_id]
    chal_ids = [c.id for c in Challenges.query.filter_by(category="crew-test").all()]

    def mine(model):
        """The rows of the test studios AND every row, of any studio, for a demo challenge (a person testing by hand in their own studio leaves some)."""
        conds = ([model.team_id.in_(team_ids)] if team_ids else []) + ([model.challenge_id.in_(chal_ids)] if chal_ids else [])
        return model.query.filter(or_(*conds)) if conds else None

    # 1. anything still running on the host is stopped with the platform's own client
    for row in (mine(L3monInstance).all() if mine(L3monInstance) is not None else []):
        if row.state in ("starting", "running"):
            try:
                get_host_tracker().get_client_for_host(row.host_name).stop_instance(row.instance_id)
            except Exception as e:  # noqa: BLE001
                out("could not stop", row.instance_id, str(e)[:80])
    # 2. the programmes, then the channel, the way the crew's own calls take them off
    serialize()
    ch = Channel.query.filter_by(slug=CHANNEL).first()
    if ch is not None:
        for p in Programme.query.filter_by(channel_id=ch.id).all():
            slug = p.slug
            db.session.delete(p)
            db.session.flush()
            audit.record("plan.remove", "programme", f"{slug} taken off the board (crew test over)")
        reconcile(locked=True)
        serialize()
        db.session.delete(ch)
        db.session.flush()
        audit.record("plan.remove", "channel", f"{CHANNEL} removed (crew test over)")
        reconcile(locked=True)
    # 3. the demo challenges and everything that hangs on them or on the test studios
    for model in (L3monInstance, L3monInstanceLog):
        query = mine(model)
        if query is not None:
            query.delete(synchronize_session=False)
    if team_ids:
        Awards.query.filter(Awards.team_id.in_(team_ids)).delete(synchronize_session=False)
    for model in (Solves, Submissions):
        if chal_ids:
            model.query.filter(model.challenge_id.in_(chal_ids)).delete(synchronize_session=False)
    if chal_ids:
        Flags.query.filter(Flags.challenge_id.in_(chal_ids)).delete(synchronize_session=False)
        Challenges.query.filter(Challenges.id.in_(chal_ids)).delete(synchronize_session=False)
    db.session.commit()
    for u in users:
        u.team_id = None
    for t in Teams.query.filter(Teams.id.in_(team_ids)).all() if team_ids else []:
        t.captain_id = None
    db.session.commit()
    if team_ids:
        Teams.query.filter(Teams.id.in_(team_ids)).delete(synchronize_session=False)
    Users.query.filter(Users.name.like(PREFIX + "-%")).delete(synchronize_session=False)
    db.session.commit()
    out("cleanup done; notifications now:", Notifications.query.count())
    return 0


def expected_tcp():
    ids = demo_ids()
    u1, t1 = make_studio(1)
    import hashlib
    out("EXPECTED_SHA", hashlib.sha256(expected_flag(t1, ids["demo-tcp"]).encode()).hexdigest()[:10])
    return 0


def counts():
    from CTFd.plugins.l3mon_core.models import Channel, Programme
    out("users", Users.query.count(), "teams", Teams.query.count(), "challenges", Challenges.query.count(), "solves", Solves.query.count(),
        "submissions", Submissions.query.count(), "channels", Channel.query.count(), "programmes", Programme.query.count())
    out("test users:", Users.query.filter(Users.name.like(PREFIX + "-%")).count())
    from CTFd.plugins.l3mon_instances.models import L3monInstance
    out("instance rows:", L3monInstance.query.count(), "| live ones:", L3monInstance.query.filter(L3monInstance.state.in_(["starting", "running"])).count())
    return 0


with app.app_context():
    sys.exit({"setup": setup, "probe": probe, "counts": counts, "setup_programme": lambda: setup(False), "functional": functional, "reset": reset, "burst": burst, "hostdown_during": hostdown_during, "hostdown_after": hostdown_after, "gone": gone, "after_gone": after_gone, "cleanup": cleanup, "expected_tcp": expected_tcp}[CMD]())
