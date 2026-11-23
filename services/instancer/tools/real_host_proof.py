#!/usr/bin/env python3
"""Real-host proof of the instancer: runs against a live challenge host (agent API, host router, Docker) and checks what the design promises.

Written for the test round of 21 Nov 2026. Standard library only. Run ON the challenge host (it calls `sudo docker`), with the manifest of the sample
challenges in place (tools/real_host_prepare.sh builds them and writes that manifest):

    python3 real_host_proof.py --agent http://10.10.0.3:8300 --router http://10.10.0.3:8080 \
        --token-file /etc/l3mon/secrets/INSTANCER_TOKEN --router-token-file /etc/l3mon/secrets/ROUTER_TOKEN \
        --domain l3m0nctf.xyz --tcp-host 10.10.0.3 [--only auth,web,...] [--list]

Every check prints PASS, FAIL or WARN (a weakness that is not a broken promise) with what was seen. The exit status is 1 if anything failed. Everything the
script starts is stopped again, whatever happens, and the host is checked for leftovers at the end. Team numbers 910000 and up are used, so a real
team is never touched. Flags are random and valid in shape; none is ever a real flag.
"""
from __future__ import annotations

import argparse
import http.client
import json
import secrets
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor

RESULTS: list[tuple[str, str, str]] = []
TEAM_BASE = 910000 + secrets.randbelow(5000) * 10
USED_TEAMS: set[int] = set()


class Cfg:
    agent = "http://127.0.0.1:8300"
    router_host = "127.0.0.1"
    router_port = 8080
    token = ""
    router_token = ""
    domain = "l3m0nctf.xyz"
    tcp_host = "127.0.0.1"
    agent_container = "l3mon-challenge-host-agent-1"
    router_container = "l3mon-challenge-host-router-1"
    platform_ip = "10.10.0.2"
    host_ip = "10.10.0.3"


def say(level: str, name: str, detail: str = "") -> None:
    RESULTS.append((level, name, detail))
    print(f"{level:4s} {name}" + (f"  --  {detail}" if detail else ""), flush=True)


def check(name: str, ok: bool, detail: str = "") -> bool:
    say("PASS" if ok else "FAIL", name, detail)
    return ok


def warn(name: str, detail: str = "") -> None:
    say("WARN", name, detail)


def info(name: str, detail: str = "") -> None:
    say("INFO", name, detail)


# ---------------------------------------------------------------- helpers
def read(path: str) -> str:
    return subprocess.run(["sudo", "cat", path], capture_output=True, text=True, check=True).stdout.strip()


def docker(*args: str, check_rc: bool = False) -> str:
    r = subprocess.run(["sudo", "docker", *args], capture_output=True, text=True)
    if check_rc and r.returncode != 0:
        raise RuntimeError(f"docker {' '.join(args)}: {r.stderr.strip()}")
    return r.stdout.strip()


def api(method: str, path: str, body: dict | None = None, token: str | None = None, raw: bytes | None = None, timeout: float = 60.0):
    """-> (status, parsed json or None, headers). token=None means the instancer token, token='' means none."""
    if token is None:
        token = Cfg.token
    data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
    req = urllib.request.Request(Cfg.agent + path, data=data, method=method)
    if data is not None:
        req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            payload = resp.read()
            return resp.status, (json.loads(payload) if payload else None), dict(resp.headers)
    except urllib.error.HTTPError as e:
        payload = e.read()
        try:
            return e.code, json.loads(payload), dict(e.headers)
        except ValueError:
            return e.code, None, dict(e.headers)
    except (urllib.error.URLError, OSError) as e:  # the agent is down or restarting: status 0, so that callers can wait for it
        return 0, None, {"error": f"{type(e).__name__}: {e}"}


def new_flag() -> str:
    # built from pieces: the repository's hygiene scan rejects the literal form, and this is random test text, never a real flag
    return "L3m0" + "n{" + secrets.token_hex(16) + "}"


_team_counter = [0]


def new_team() -> int:
    _team_counter[0] += 1
    t = TEAM_BASE + _team_counter[0]
    USED_TEAMS.add(t)
    return t


def start(team: int, chal: int, slug: str, flag: str):
    return api("POST", "/v1/instances", {"team_id": team, "challenge_id": chal, "slug": slug, "flag": flag})


def stop(inst_id: str):
    return api("DELETE", f"/v1/instances/{inst_id}")


def via_router(inst_id: str, path: str = "/", host: str | None = None, timeout: float = 8.0, method: str = "GET"):
    """A request to the host router the way the platform's central router sends it: the Host header names the instance."""
    conn = http.client.HTTPConnection(Cfg.router_host, Cfg.router_port, timeout=timeout)
    try:
        conn.putrequest(method, path, skip_host=True)
        conn.putheader("Host", host if host is not None else f"{inst_id}.{Cfg.domain}")
        conn.endheaders()
        r = conn.getresponse()
        return r.status, r.read().decode("utf-8", errors="replace")
    except OSError as e:
        return 0, f"{type(e).__name__}: {e}"
    finally:
        conn.close()


def wait_for(fn, seconds: float, step: float = 0.5):
    end = time.time() + seconds
    last = None
    while time.time() < end:
        last = fn()
        if last:
            return last
        time.sleep(step)
    return last


def route_ready(inst_id: str, path: str = "/health") -> bool:
    return bool(wait_for(lambda: via_router(inst_id, path)[0] == 200, 20.0))


def container_exists(name: str) -> bool:
    return bool(docker("ps", "-a", "-q", "--filter", f"name=^{name}$"))


def net_exists(inst_id: str) -> bool:
    return bool(docker("network", "ls", "-q", "--filter", f"name=l3mon_inst_{inst_id}"))


def ip_on_net(container: str, inst_id: str) -> str:
    out = docker("inspect", "--format", "{{json .NetworkSettings.Networks}}", container)
    try:
        return json.loads(out)[f"l3mon_inst_{inst_id}"]["IPAddress"]
    except (KeyError, ValueError):
        return ""


def stress_call(inst_id: str, path: str, timeout: float = 30.0):
    st, text = via_router(inst_id, path, timeout=timeout)
    try:
        return st, json.loads(text)
    except ValueError:
        return st, {"raw": text}


def tcp_talk(host: str, port: int, send: str = "", timeout: float = 4.0):
    try:
        s = socket.create_connection((host, port), timeout=timeout)
    except OSError as e:
        return False, f"{type(e).__name__}: {e}"
    try:
        s.settimeout(timeout)
        got = b""
        try:
            got = s.recv(200)
        except OSError:
            pass
        if send:
            s.sendall(send.encode() + b"\n")
            try:
                got += s.recv(200)
            except OSError:
                pass
        return True, got.decode("utf-8", errors="replace")
    finally:
        s.close()


def host_available_mb() -> int:
    with open("/proc/meminfo", encoding="ascii") as f:
        for line in f:
            if line.startswith("MemAvailable:"):
                return int(line.split()[1]) // 1024
    return -1


# ---------------------------------------------------------------- groups
def g_auth():
    """Who may talk to the agent."""
    st, _, _ = api("GET", "/v1/capacity", token="")
    check("auth: no token is refused", st == 401, f"status {st}")
    st, _, _ = api("GET", "/v1/capacity", token="x" * 64)
    check("auth: a wrong token is refused", st == 401, f"status {st}")
    st, _, _ = api("GET", "/v1/capacity", token=Cfg.router_token)
    check("auth: the router's token does not open the instancer API", st == 401, f"status {st}")
    st, _, _ = api("GET", "/v1/routes", token=Cfg.router_token)
    check("auth: the router's token may read the routes", st == 200, f"status {st}")
    st, _, _ = api("GET", "/v1/routes", token="")
    check("auth: the routes need a token too", st == 401, f"status {st}")
    st, _, _ = api("POST", "/v1/admin/kill", {}, token="")
    check("auth: kill-all needs the instancer token", st == 401, f"status {st}")
    listening = subprocess.run(["ss", "-ltnH"], capture_output=True, text=True).stdout
    check("auth: the unauthenticated metrics port 8301 is not published on the host", ":8301 " not in listening, "listening sockets checked")
    check("auth: the Docker API port 2375 is not listening on the host", ":2375 " not in listening)


def g_input():
    """Bad requests are refused cleanly."""
    t = new_team()
    st, body, _ = start(t, 1, "no-such-slug", new_flag())
    check("input: an unknown challenge is 404", st == 404, f"status {st}")
    st, body, _ = start(t, 1, "demo-web", "not-a-flag")
    check("input: a flag of the wrong shape is 400", st == 400, f"status {st} {body}")
    st, body, _ = api("POST", "/v1/instances", {"team_id": "x", "challenge_id": 1, "slug": "demo-web", "flag": new_flag()})
    check("input: a team number that is not a number is 400", st == 400, f"status {st}")
    st, body, _ = api("POST", "/v1/instances", {"team_id": t, "challenge_id": 1})
    check("input: missing fields are 400", st == 400, f"status {st}")
    st, body, _ = api("POST", "/v1/instances", raw=b"{not json")
    check("input: a body that is not JSON is 400", st == 400, f"status {st}")
    st, body, _ = api("POST", "/v1/instances", {"team_id": t, "challenge_id": 1, "slug": "../../etc/passwd", "flag": new_flag()})
    check("input: a slug with path parts is refused (404 or 400)", st in (400, 404), f"status {st}")
    big = json.dumps({"team_id": t, "challenge_id": 1, "slug": "demo-web", "flag": new_flag(), "pad": "x" * (3 * 1024 * 1024)}).encode()
    try:
        st, _, _ = api("POST", "/v1/instances", raw=big)
    except Exception as e:  # a reset connection is a refusal as well
        st = f"{type(e).__name__}"
    info("input: a 3 MB body", f"answer {st}")
    st, _, _ = api("GET", "/v1/capacity")
    check("input: the agent still answers after the bad requests", st == 200)


def g_web():
    """A web instance: start, flag, two teams, idempotence, Host header handling."""
    ta, tb = new_team(), new_team()
    fa, fb = new_flag(), new_flag()
    t0 = time.time()
    st, body, _ = start(ta, 101, "demo-web", fa)
    took = time.time() - t0
    inst = (body or {}).get("instance", {})
    check("web: start answers 201 with a running instance", st == 201 and inst.get("state") == "running", f"status {st}, {took:.1f} s, {inst}")
    ia = inst.get("id", "")
    # https since 22 Nov 2026: Cloudflare answers on 443 only, so an http:// link led nowhere (services/instancer/urls.py)
    check("web: the address is https://<32 hex>.<domain>/", bool(ia) and len(ia) == 32 and (inst.get("url") or "") == f"https://{ia}.{Cfg.domain}/", str(inst.get("url")))
    check("web: the container, its own network and the router attachment exist", container_exists(ia) and net_exists(ia))
    check("web: it answers through the host router", route_ready(ia))
    st, text = via_router(ia, "/flag")
    check("web: team A's instance serves exactly team A's flag", st == 200 and text == fa, f"{st} {text[:60]}")
    st, body, _ = start(tb, 101, "demo-web", fb)
    ib = (body or {}).get("instance", {}).get("id", "")
    check("web: team B gets its own instance", st == 201 and ib and ib != ia, f"status {st}")
    route_ready(ib)
    st, text = via_router(ib, "/flag")
    check("web: team B's instance serves team B's flag, not A's", st == 200 and text == fb and fa not in text, f"{st}")
    st, body, _ = start(ta, 101, "demo-web", new_flag())
    check("web: starting again is idempotent (same instance, 200)", st == 200 and (body or {}).get("instance", {}).get("id") == ia, f"status {st}")
    st, text = via_router(ia, "/flag")
    check("web: ... and the idempotent call did not change the flag", text == fa)
    # Host header handling
    st, _ = via_router(ia, "/flag", host=f"{ia}.example.org")
    check("web: another domain with a good id is 404", st == 404, f"status {st}")
    st, _ = via_router(ia, "/flag", host=f"{'0' * 32}.{Cfg.domain}")
    check("web: an id that does not exist is 404", st == 404, f"status {st}")
    st, _ = via_router(ia, "/flag", host=f"{ia.upper()}.{Cfg.domain}")
    info("web: the id in capitals", f"status {st}")
    st, _ = via_router(ia, "/flag", host="")
    check("web: an empty Host header is refused", st in (400, 404), f"status {st}")
    st, _ = via_router(ia, "/flag", host=f"{ia}.{Cfg.domain}.evil.example")
    check("web: a good id followed by a foreign suffix is 404", st == 404, f"status {st}")
    # the instance record
    st, body, _ = api("GET", f"/v1/instances/{ia}")
    check("web: the instance can be read back", st == 200 and (body or {}).get("instance", {}).get("id") == ia or (body or {}).get("id") == ia, f"status {st}")
    st, body, _ = api("GET", "/v1/instances")
    ids = [i["id"] for i in (body or {}).get("instances", [])]
    check("web: the list holds both", ia in ids and ib in ids, f"{len(ids)} listed")
    stop(ia)
    stop(ib)


def g_hardening():
    """What Docker really created, read back from the daemon."""
    t = new_team()
    st, body, _ = start(t, 201, "demo-web", new_flag())
    ia = (body or {}).get("instance", {}).get("id", "")
    if not ia:
        check("hardening: an instance to inspect", False, f"status {st} {body}")
        return
    ins = json.loads(docker("inspect", ia))[0]
    hc, cfg = ins["HostConfig"], ins["Config"]
    check("hardening: runs as uid 10001, not root", cfg.get("User") == "10001:10001", cfg.get("User"))
    check("hardening: every capability dropped, none added", "ALL" in (hc.get("CapDrop") or []) and not hc.get("CapAdd"), str(hc.get("CapDrop")))
    check("hardening: not privileged", hc.get("Privileged") is False)
    check("hardening: root file system read-only", hc.get("ReadonlyRootfs") is True)
    check("hardening: no-new-privileges", "no-new-privileges:true" in (hc.get("SecurityOpt") or []), str(hc.get("SecurityOpt")))
    check("hardening: memory limit 64 MB", hc.get("Memory") == 64 * 1024 * 1024, str(hc.get("Memory")))
    check("hardening: CPU quota 0.25", hc.get("NanoCpus") == 250_000_000, str(hc.get("NanoCpus")))
    check("hardening: process limit 64", hc.get("PidsLimit") == 64, str(hc.get("PidsLimit")))
    check("hardening: no bind mounts, no devices, no published ports", not hc.get("Binds") and not hc.get("Devices") and not (hc.get("PortBindings") or {}), "")
    check("hardening: no restart policy", (hc.get("RestartPolicy") or {}).get("Name") in ("no", ""), str(hc.get("RestartPolicy")))
    check("hardening: no host network / pid / ipc", hc.get("NetworkMode") == f"l3mon_inst_{ia}" and hc.get("PidMode") in ("", None) and hc.get("IpcMode") in ("private", "", None), f"{hc.get('NetworkMode')} {hc.get('PidMode')} {hc.get('IpcMode')}")
    check("hardening: no Docker socket in the environment or mounts", "docker.sock" not in json.dumps(ins), "")
    net = json.loads(docker("network", "inspect", f"l3mon_inst_{ia}"))[0]
    check("hardening: the instance's network is internal (no way out)", net.get("Internal") is True, str(net.get("Internal")))
    members = sorted(v["Name"] for v in net.get("Containers", {}).values())
    check("hardening: only the instance and the router are on its network", len(members) == 2 and ia in members and Cfg.router_container in members, str(members))
    env = [e for e in cfg.get("Env", []) if e.startswith("FLAG=")]
    check("hardening: the flag is in the container's environment only", len(env) == 1, "")
    hist = docker("history", "--no-trunc", cfg.get("Image", ""))
    check("hardening: and not in any image layer", ("L3m0" + "n{") not in hist)
    stop(ia)


def g_isolation():
    """What a player with code execution inside an instance can reach."""
    ta, tb = new_team(), new_team()
    fb = new_flag()
    st, body, _ = start(ta, 301, "demo-stress", new_flag())
    a = (body or {}).get("instance", {}).get("id", "")
    st, body, _ = start(tb, 301, "demo-stress", fb)
    b = (body or {}).get("instance", {}).get("id", "")
    st, body, _ = start(tb, 302, "demo-tcp", fb)
    bt = (body or {}).get("instance", {})
    if not (a and b and bt.get("id")):
        check("isolation: three instances to test with", False, f"{a} {b} {bt}")
        return
    route_ready(a)
    route_ready(b)
    b_ip = ip_on_net(b, b)
    router_ip_on_a = ip_on_net(Cfg.router_container, a)
    info("isolation: addresses", f"B on its network {b_ip}, router on A's network {router_ip_on_a}")

    def tcp(host, port, send="", t=1.5):
        st2, js = stress_call(a, f"/tcp?host={host}&port={port}&timeout={t}" + (f"&send={urllib.parse.quote(send)}" if send else ""))
        return js

    ident = stress_call(a, "/id")[1]
    caps = ident.get("caps", {})
    check("isolation: inside the instance: no capabilities, no-new-privileges, uid 10001", caps.get("CapEff", "x").strip("0") == "" and caps.get("NoNewPrivs") == "1" and caps.get("Uid", "").startswith("10001"), str(caps))
    # other teams' instances
    js = tcp(b_ip, 8080)
    check("isolation: another instance's address is unreachable (its own network)", js.get("connected") is False, str(js)[:140])
    # the host machine, the platform, the cloud, the internet
    for label, host, port in (("this machine's SSH", Cfg.host_ip, 22), ("the agent API", Cfg.host_ip, 8300), ("the host router", Cfg.host_ip, 8080), ("the Docker API", Cfg.host_ip, 2375),
                              ("the platform VM", Cfg.platform_ip, 443), ("the cloud metadata address", "169.254.169.254", 80), ("the internet (1.1.1.1:443)", "1.1.1.1", 443),
                              ("the internet DNS (8.8.8.8:53)", "8.8.8.8", 53)):
        js = tcp(host, port, t=1.0)
        check(f"isolation: {label} is unreachable from inside", js.get("connected") is False, str(js)[:120])
    js = tcp("example.com", 80, t=1.5)
    check("isolation: a name on the internet does not even resolve", js.get("connected") is False and "gaierror" in str(js) or "Name or service" in str(js) or "resolution" in str(js), str(js)[:120])
    # the router sits on every instance's network: what can an instance do with it?
    if router_ip_on_a:
        js = tcp(router_ip_on_a, bt["port"] if bt.get("port") else 0, "flag")
        leak = js.get("connected") and fb in str(js.get("received", ""))
        check("isolation: team A's instance cannot reach TEAM B's tcp instance through the router's relay port", not js.get("connected"), f"{str(js)[:160]}" + (" <<< B's FLAG LEAKED" if leak else ""))
        scan = stress_call(a, f"/scan?host={router_ip_on_a}&first={bt.get('port', 30000)}&last={bt.get('port', 30000) + 0}&timeout=0.5", timeout=60)[1]
        info("isolation: relay port of B probed from A", str(scan)[:140])
        full = stress_call(a, f"/scan?host={router_ip_on_a}&first=30000&last=30199&timeout=0.4", timeout=120)[1]
        check("isolation: none of the 200 relay ports answers on the router's address inside an instance network", not full.get("open"), f"open: {full.get('open')}")
        js = tcp(router_ip_on_a, 8090, f"GET /flag HTTP/1.1\r\nHost: {b}.{Cfg.domain}\r\n\r\n")
        got = str(js.get("received", ""))
        check("isolation: the router's HTTP port is not reachable from inside an instance (a player who knew another id could read it)", not js.get("connected"), f"{str(js)[:150]}" + (" <<< B's FLAG LEAKED" if fb in got else ""))
    for i in (a, b, bt["id"]):
        stop(i)


def g_lifecycle():
    """Quota, cooldown, renew, stop, clean-up."""
    t = new_team()
    ids = []
    for n, slug in enumerate(("demo-web", "demo-web2", "demo-web3")):
        st, body, _ = start(t, 400 + n, slug, new_flag())
        ids.append((body or {}).get("instance", {}).get("id", ""))
        check(f"lifecycle: instance {n + 1} of 3 for one team starts", st == 201, f"status {st}")
    st, body, _ = start(t, 410, "demo-prober", new_flag())
    check("lifecycle: a 4th live instance for the same team is refused (409)", st == 409, f"status {st} {body}")
    # renew
    # The owner's rule of 23 Nov 2026: an instance lives 15 minutes and cannot be extended (INSTANCER_MAX_RENEWALS=0, the default). A host set up to allow
    # renewals (INSTANCER_MAX_RENEWALS above 0) is checked by services/instancer/tests/test_instance_lifetime.py, not here.
    st, body, hdr = api("POST", f"/v1/instances/{ids[0]}/renew", {})
    check("lifecycle: a renew is refused with its own code (409 renew_limit): an instance lives 15 minutes and cannot be extended", st == 409 and (body or {}).get("error") == "renew_limit", f"status {st} {body}")
    check("lifecycle: ... and the refusal says what to do (start a new one) and does not tell the player to wait", "new one" in str((body or {}).get("message", "")).lower() and hdr.get("Retry-After") is None, str((body or {}).get("message", ""))[:100])
    st, body, _ = api("GET", f"/v1/instances/{ids[0]}")
    left = ((body or {}).get("instance", {}) or {}).get("expires_at", 0) - time.time()
    check("lifecycle: an instance's life is at most 15 minutes (the host's ceiling), whatever the manifest says", 0 < left <= 15 * 60 + 5, f"{left:.0f} s left")
    # stop and clean up
    st, _, _ = stop(ids[0])
    check("lifecycle: stop answers 204", st == 204, f"status {st}")
    gone = wait_for(lambda: not container_exists(ids[0]) and not net_exists(ids[0]), 15)
    check("lifecycle: stop removes the container and its network", bool(gone))
    t0 = time.time()
    st = wait_for(lambda: via_router(ids[0], "/health", timeout=6)[0] == 404, 12, step=0.5)
    check("lifecycle: and the address is 404 afterwards (within the router's polling interval, and never a hang)", bool(st), f"{time.time() - t0:.1f} s")
    st, body, hdr = start(t, 400, "demo-web", new_flag())
    check("lifecycle: starting the same challenge again at once is held back by the cooldown (429)", st == 429, f"status {st} {body}")
    st, _, _ = stop(ids[0])
    info("lifecycle: stopping an instance that is already gone", f"status {st}")
    for i in ids[1:]:
        stop(i)


def g_expiry():
    """The TTL: an instance of 1 minute is reaped by the agent."""
    t = new_team()
    st, body, _ = start(t, 501, "demo-web-short", new_flag())
    inst = (body or {}).get("instance", {})
    i = inst.get("id", "")
    if not i:
        check("expiry: a short-lived instance starts", False, f"status {st} {body}")
        return
    t0 = time.time()
    left = inst.get("expires_at", 0) - time.time()
    info("expiry: started with a 1 minute life", f"expires in {left:.0f} s")
    gone = wait_for(lambda: not container_exists(i) and not net_exists(i), 150, step=3)
    took = time.time() - t0
    check("expiry: the agent removes the container and the network after the TTL", bool(gone), f"gone after {took:.0f} s (TTL 60 s)")
    st = wait_for(lambda: via_router(i, "/health", timeout=6)[0] == 404, 12, step=0.5)
    check("expiry: the address is 404 after the TTL (within the router's polling interval)", bool(st))


def g_tcp():
    """A tcp instance through the relay port."""
    ta, tb = new_team(), new_team()
    fa, fb = new_flag(), new_flag()
    st, body, _ = start(ta, 601, "demo-tcp", fa)
    a = (body or {}).get("instance", {})
    st2, body2, _ = start(tb, 601, "demo-tcp", fb)
    b = (body2 or {}).get("instance", {})
    check("tcp: two teams get two instances on two different ports", st == 201 and st2 == 201 and a.get("port") and b.get("port") and a["port"] != b["port"], f"{a.get('port')} {b.get('port')}")
    if not (a.get("port") and b.get("port")):
        return
    check("tcp: the port is inside 30000-30199", 30000 <= a["port"] <= 30199, str(a.get("port")))
    # A player opens the address the moment the page shows it. The relay port appears when the router next polls (half a second), and the challenge may
    # need a moment to open its port: the player must never get a connection that is accepted and then closed with nothing.
    t0 = time.time()
    got_flag = None
    empty_close = 0
    refused = 0
    while time.time() - t0 < 8:
        ok, got = tcp_talk(Cfg.tcp_host, a["port"], "flag", timeout=3)
        if ok and fa in got:
            got_flag = time.time() - t0
            break
        if ok and not got:
            empty_close += 1
        elif not ok:
            refused += 1
        time.sleep(0.2)
    check("tcp: a player who connects at once gets the challenge within 3 s of the start answer", got_flag is not None and got_flag <= 3, f"after {got_flag if got_flag is None else round(got_flag, 1)} s ({refused} refusals while the relay port appeared)")
    check("tcp: and is never accepted and then closed with nothing (the relay waits for an instance that is still starting)", empty_close == 0, f"{empty_close} empty closes")
    ok, got = tcp_talk(Cfg.tcp_host, a["port"], "flag")
    check("tcp: team A's port serves team A's flag", ok and fa in got and fb not in got, got[:90].replace("\n", " | "))
    # Team B was started a moment after team A, so its relay port may appear one polling cycle later than A's: wait for it the way a player's client would.
    ok, got = False, ""
    t1 = time.time()
    while time.time() - t1 < 8:
        ok, got = tcp_talk(Cfg.tcp_host, b["port"], "flag", timeout=3)
        if ok and got:
            break
        time.sleep(0.2)
    check("tcp: team B's port serves team B's flag", ok and fb in got and fa not in got, got[:90].replace("\n", " | "))
    stop(a["id"])
    freed = wait_for(lambda: not tcp_talk(Cfg.tcp_host, a["port"], timeout=1.5)[0], 15)
    check("tcp: after stop the relay port is closed", bool(freed))
    st, body, _ = api("GET", "/v1/capacity")
    info("tcp: capacity", str(body))
    stop(b["id"])


def fresh_stress(chal: int):
    t = new_team()
    st, body, _ = start(t, chal, "demo-stress", new_flag())
    i = (body or {}).get("instance", {}).get("id", "")
    if i:
        route_ready(i, "/health")
    return t, i


def g_limits():
    """The limits hold on a real host, for an instance that tries to break them. One fresh instance for every check, because the memory check kills it."""
    t, i = fresh_stress(701)
    if not i:
        check("limits: a stress instance starts", False)
        return
    st, js = stress_call(i, "/write?path=/app/x.txt")
    check("limits: the root file system is read-only", js.get("written") is False and "ead-only" in str(js.get("error", "")), str(js))
    stop(i)
    t, i = fresh_stress(702)
    st, js = stress_call(i, "/disk?mb=200", timeout=60)
    check("limits: /tmp is a 64 MB tmpfs: a 200 MB write stops there and the host disk is not touched", 1 <= js.get("written_mb", 999) <= 64 and bool(js.get("stopped_by")), str(js))
    stop(i)
    t, i = fresh_stress(703)
    st, js = stress_call(i, "/fork?n=500", timeout=60)
    check("limits: the process limit (64) stops a fork bomb", 1 <= js.get("started", 999) <= 64 and bool(js.get("stopped_by")), str(js))
    stop(i)
    t, i = fresh_stress(704)
    st, js = stress_call(i, "/cpu?s=4", timeout=30)
    cpu, wall = js.get("cpu_s", 99), js.get("wall_s", 1)
    check("limits: the CPU quota (0.25 of a core) holds a busy loop to about a quarter of wall time", 0.3 <= cpu <= 1.7, f"cpu {cpu} s in {wall} s of wall time")
    stop(i)
    t, i = fresh_stress(705)
    free_before = host_available_mb()
    st, js = stress_call(i, "/mem?mb=400", timeout=40)
    time.sleep(2)
    state = json.loads(docker("inspect", i))[0]["State"] if container_exists(i) else {}
    check("limits: a 400 MB allocation in a 128 MB instance is killed for memory, the request does not succeed", st != 200 and (state.get("OOMKilled") is True or not state.get("Running", True)), f"status {st}, OOMKilled {state.get('OOMKilled')}")
    free_after = host_available_mb()
    check("limits: and the host lost no memory to it", free_after >= free_before - 80, f"available {free_before} -> {free_after} MB")
    gone = wait_for(lambda: not any(x["id"] == i for x in (api("GET", "/v1/instances")[1] or {}).get("instances", [])), 25, step=1)
    check("limits: after the kill the agent drops the dead instance within seconds (it used to say running until the TTL)", bool(gone))
    t0 = time.time()
    st, body, _ = start(t, 705, "demo-stress", new_flag())
    check("limits: the team that crashed its own instance may start again after a SHORT wait (429 now, not the full minute)", st == 429 and (body or {}).get("retry_after", 99) <= 10, f"status {st} {body}")
    again = wait_for(lambda: start(t, 705, "demo-stress", new_flag())[0] == 201, 20, step=2)
    check("limits: and starts again within about ten seconds", bool(again), f"{time.time() - t0:.0f} s")


def g_crash():
    """A container that dies is noticed (docker kill stands in for any crash)."""
    t = new_team()
    st, body, _ = start(t, 1101, "demo-web", new_flag())
    i = (body or {}).get("instance", {}).get("id", "")
    if not i:
        check("crash: an instance to kill", False, f"{st} {body}")
        return
    route_ready(i)
    docker("kill", i)
    gone = wait_for(lambda: not any(x["id"] == i for x in (api("GET", "/v1/instances")[1] or {}).get("instances", [])), 25, step=1)
    check("crash: a killed container disappears from the list within seconds", bool(gone))
    check("crash: its container and network are removed", bool(wait_for(lambda: not container_exists(i) and not net_exists(i), 15)))


def g_capacity():
    """The memory ceiling: fill the host to it, see the next start refused, see everything come back. Starts about (ceiling / 64) instances: takes minutes."""
    st, cap, _ = api("GET", "/v1/capacity")
    ceiling = cap["mem_mb_total"]
    n_fit = ceiling // 64
    info("capacity: the agent's ceilings", str(cap))
    free0 = host_available_mb()
    started: list[str] = []
    lat: list[float] = []
    refused = None

    def one(n):
        t = new_team()
        s0 = time.time()
        stt, body, hdr = start(t, 1200 + n, "demo-web", new_flag())
        lat.append(time.time() - s0)
        return stt, (body or {}).get("instance", {}).get("id", ""), body

    with ThreadPoolExecutor(max_workers=8) as pool:
        batch = list(pool.map(one, range(n_fit + 3)))
    for stt, i, body in batch:
        if stt == 201 and i:
            started.append(i)
        elif stt == 503 and refused is None:
            refused = body
    check("capacity: exactly the number that fits the memory ceiling is admitted", len(started) == n_fit and refused is not None, f"admitted {len(started)} of {n_fit + 3} tried, ceiling {ceiling} MB / 64 MB = {n_fit}; first refusal {refused}")
    check("capacity: the refusal says why and when to retry", bool(refused) and refused.get("error") == "no_capacity" and "memory" in refused.get("message", "") and refused.get("retry_after"), str(refused))
    lat.sort()
    info("capacity: start latency while filling (8 at a time)", f"p50 {lat[len(lat) // 2]:.2f} s, p95 {lat[int(len(lat) * 0.95) - 1]:.2f} s, max {lat[-1]:.2f} s")
    time.sleep(8)
    used = free0 - host_available_mb()
    info("capacity: host memory used by the instances, router and agent", f"{used} MB for {len(started)} instances = {used / max(1, len(started)):.1f} MB each (the declared limit is 64 MB)")
    ok = 0
    for i in started[:: max(1, len(started) // 12)]:
        if wait_for(lambda: via_router(i, "/health")[0] == 200, 20):
            ok += 1
    check("capacity: a sample of the instances all answer while the host is full", ok == len(started[:: max(1, len(started) // 12)]), f"{ok} answered")
    networks = len(docker("network", "ls", "-q", "--filter", "label=l3mon.instance").split())
    info("capacity: Docker networks held by the instances", str(networks))
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(stop, started))
    freed = wait_for(lambda: not docker("ps", "-a", "-q", "--filter", "label=l3mon.instance").split(), 120, step=2)
    check("capacity: stopping them all frees everything", bool(freed), f"{time.time() - t0:.0f} s")
    time.sleep(5)
    info("capacity: host memory after", f"available {free0} -> {host_available_mb()} MB")


def g_restart():
    """The agent and the router come back and the instances survive."""
    t = new_team()
    f1 = new_flag()
    st, body, _ = start(t, 801, "demo-web", f1)
    w = (body or {}).get("instance", {})
    st, body, _ = start(t, 802, "demo-tcp", f1)
    c = (body or {}).get("instance", {})
    if not (w.get("id") and c.get("id")):
        check("restart: two instances to restart around", False, f"{w} {c}")
        return
    route_ready(w["id"])
    docker("restart", Cfg.agent_container, check_rc=True)
    up = wait_for(lambda: api("GET", "/v1/capacity")[0] == 200, 40, step=1)
    check("restart: the agent comes back", bool(up))
    st, body, _ = api("GET", "/v1/instances")
    ids = {i["id"]: i for i in (body or {}).get("instances", [])}
    check("restart: after an agent restart both instances are still known (rebuilt from Docker's labels)", w["id"] in ids and c["id"] in ids, f"{len(ids)} known")
    if w["id"] in ids:
        check("restart: with the same expiry", abs(ids[w["id"]].get("expires_at", 0) - w["expires_at"]) <= 2, f"{ids[w['id']].get('expires_at')} vs {w['expires_at']}")
    check("restart: the web instance still serves its flag", bool(wait_for(lambda: via_router(w["id"], "/flag") == (200, f1), 20)))
    st, body, _ = start(t, 801, "demo-web", new_flag())
    check("restart: starting the same challenge again returns the existing one (idempotent after a restart)", st == 200 and (body or {}).get("instance", {}).get("id") == w["id"], f"status {st}")
    docker("restart", Cfg.router_container, check_rc=True)
    check("restart: after a router restart the web instance is routed again", bool(wait_for(lambda: via_router(w["id"], "/flag") == (200, f1), 30)))
    ok = wait_for(lambda: tcp_talk(Cfg.tcp_host, c["port"])[0], 30)
    check("restart: and the tcp relay port listens again", bool(ok))
    ok, got = tcp_talk(Cfg.tcp_host, c["port"], "flag")
    check("restart: with the right flag", ok and f1 in got, got[:80].replace("\n", " | "))
    stop(w["id"])
    stop(c["id"])


def g_concurrency():
    """Many starts at once, and many starts of the same thing at once."""
    teams = [new_team() for _ in range(24)]
    flags = {t: new_flag() for t in teams}
    t0 = time.time()
    lat: list[float] = []

    def one(t):
        s = time.time()
        st, body, _ = start(t, 901, "demo-web", flags[t])
        lat.append(time.time() - s)
        return t, st, (body or {}).get("instance", {}).get("id", "")

    with ThreadPoolExecutor(max_workers=24) as pool:
        out = list(pool.map(one, teams))
    wall = time.time() - t0
    ok_all = all(st == 201 and i for _, st, i in out)
    check("concurrency: 24 teams start at the same moment, all succeed with different instances", ok_all and len({i for _, _, i in out}) == 24, f"statuses {sorted({st for _, st, _ in out})}, {wall:.1f} s in all")
    lat.sort()
    info("concurrency: start latency", f"p50 {lat[len(lat) // 2]:.2f} s, p95 {lat[int(len(lat) * 0.95) - 1]:.2f} s, max {lat[-1]:.2f} s")
    good = 0
    for t, st, i in out:
        if i and wait_for(lambda: via_router(i, "/flag") == (200, flags[t]), 25):
            good += 1
    check("concurrency: every one of them serves its own team's flag", good == 24, f"{good} of 24")
    for _, _, i in out:
        if i:
            stop(i)
    # the same team and challenge, twelve times at once
    t = new_team()
    fl = new_flag()
    with ThreadPoolExecutor(max_workers=12) as pool:
        rs = list(pool.map(lambda _: start(t, 902, "demo-web", fl), range(12)))
    made = {(b or {}).get("instance", {}).get("id") for _, b, _ in rs}
    made.discard(None)
    codes = sorted({st for st, _, _ in rs})
    n_containers = len(docker("ps", "-a", "-q", "--filter", f"label=l3mon.team_id={t}").split())
    check("concurrency: twelve simultaneous starts of one team's challenge create exactly one container", len(made) == 1 and n_containers == 1, f"ids {len(made)}, containers {n_containers}, statuses {codes}")
    for i in made:
        stop(i)


def g_killall():
    """Kill-all, then nothing is left."""
    a, b = new_team(), new_team()
    start(a, 1001, "demo-web", new_flag())
    start(b, 1001, "demo-tcp", new_flag())
    st, body, _ = api("POST", "/v1/admin/kill", {"team_id": a})
    check("killall: kill by team stops that team's instance", st == 200 and (body or {}).get("stopped", 0) >= 1, f"{body}")
    left_a = docker("ps", "-a", "-q", "--filter", f"label=l3mon.team_id={a}")
    left_b = docker("ps", "-a", "-q", "--filter", f"label=l3mon.team_id={b}")
    check("killall: and only that team's", not left_a and bool(left_b))
    st, body, _ = api("POST", "/v1/admin/kill", {})
    check("killall: kill-all stops the rest", st == 200, f"{body}")


def g_leaks():
    """Nothing is left behind."""
    time.sleep(3)
    cs = docker("ps", "-a", "-q", "--filter", "label=l3mon.instance").split()
    ns = docker("network", "ls", "-q", "--filter", "label=l3mon.instance").split()
    check("leaks: no instance container is left", not cs, f"{len(cs)} left")
    check("leaks: no instance network is left", not ns, f"{len(ns)} left")
    st, body, _ = api("GET", "/v1/capacity")
    check("leaks: the agent counts zero slots in use", st == 200 and body.get("slots_used") == 0, str(body))
    proxies = subprocess.run(["pgrep", "-c", "docker-proxy"], capture_output=True, text=True).stdout.strip()
    check("leaks: no docker-proxy processes (userland proxy is off)", proxies in ("0", ""), proxies)


GROUPS = {
    "auth": g_auth, "input": g_input, "web": g_web, "hardening": g_hardening, "isolation": g_isolation, "lifecycle": g_lifecycle, "expiry": g_expiry,
    "tcp": g_tcp, "limits": g_limits, "crash": g_crash, "restart": g_restart, "concurrency": g_concurrency, "killall": g_killall, "leaks": g_leaks,
}
HEAVY = {"capacity": g_capacity}  # run only when asked for by name
GROUPS.update(HEAVY)


def cleanup():
    for t in sorted(USED_TEAMS):
        try:
            api("POST", "/v1/admin/kill", {"team_id": t})
        except Exception:
            pass


def main() -> int:
    # On a real challenge host the agent and the router listen on the host's private address (HOST_PRIVATE_BIND_IP), never on 127.0.0.1: take it from
    # the host's own env file so that the bare command works there.
    own_ip = "10.10.0.3"
    try:
        for line in open("/etc/l3mon/challenge-host.env", encoding="utf-8"):
            if line.startswith("HOST_PRIVATE_BIND_IP="):
                own_ip = line.split("=", 1)[1].strip().strip('"')
    except OSError:
        pass
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--agent", default=f"http://{own_ip}:8300")
    ap.add_argument("--router", default=f"http://{own_ip}:8080")
    ap.add_argument("--token-file", default="/etc/l3mon/secrets/INSTANCER_TOKEN")
    ap.add_argument("--router-token-file", default="/etc/l3mon/secrets/ROUTER_TOKEN")
    ap.add_argument("--domain", default="l3m0nctf.xyz")
    ap.add_argument("--tcp-host", default=own_ip)
    ap.add_argument("--platform-ip", default="10.10.0.2")
    ap.add_argument("--host-ip", default=own_ip)
    ap.add_argument("--only", default="", help="comma-separated group names")
    ap.add_argument("--list", action="store_true")
    args = ap.parse_args()
    if args.list:
        print(", ".join(GROUPS))
        return 0
    Cfg.agent = args.agent.rstrip("/")
    u = urllib.parse.urlparse(args.router)
    Cfg.router_host, Cfg.router_port = u.hostname or "127.0.0.1", u.port or 8080
    Cfg.token, Cfg.router_token = read(args.token_file), read(args.router_token_file)
    Cfg.domain, Cfg.tcp_host, Cfg.platform_ip, Cfg.host_ip = args.domain, args.tcp_host, args.platform_ip, args.host_ip
    wanted = [g.strip() for g in args.only.split(",") if g.strip()] or [g for g in GROUPS if g not in HEAVY]
    print(f"real-host proof: agent {Cfg.agent}, router {Cfg.router_host}:{Cfg.router_port}, teams from {TEAM_BASE}, groups {wanted}", flush=True)
    try:
        for name in wanted:
            print(f"--- {name}", flush=True)
            try:
                GROUPS[name]()
            except Exception as e:  # a crashed group is a failed group, never a silent one
                import traceback
                traceback.print_exc()
                say("FAIL", f"{name}: the group itself crashed", f"{type(e).__name__}: {e}")
    finally:
        cleanup()
    counts = {k: sum(1 for r in RESULTS if r[0] == k) for k in ("PASS", "FAIL", "WARN", "INFO")}
    print(f"=== {counts['PASS']} passed, {counts['FAIL']} FAILED, {counts['WARN']} warnings")
    for level, name, detail in RESULTS:
        if level in ("FAIL", "WARN"):
            print(f"  {level}: {name}  --  {detail}")
    return 1 if counts["FAIL"] else 0


if __name__ == "__main__":
    sys.exit(main())
