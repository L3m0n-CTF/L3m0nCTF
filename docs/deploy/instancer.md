# Challenge Instancer & Router Deployment Guide (`services/instancer`)

This document describes how to configure, deploy, and operate the challenge instancer service, reverse-proxy router, and multi-host fleet (SP4 / Stream F) for L3m0nCTF 2026.

---

## 1. Architecture & Design Principles

- **Zero Touch to CTFd Core:** Instance management is orchestrated via the `plugins/l3mon_instances` CTFd plugin. CTFd's internal files and core models remain unedited.
- **Strict Host & Container Isolation:**
  - Instanced containers run as unprivileged numeric UIDs (`user: "10001:10001"`).
  - All containers drop all capabilities (`cap_drop: [ALL]`), enforce `no-new-privileges: true`, have strict memory (`mem_limit`) and PID (`pids_limit`) boundaries, and run with read-only root filesystems (`read_only: true`).
  - Network isolation: Every instance lives in its own dedicated Docker bridge network with `internal: true` (no outbound route to the internet, gateway, or metadata services). Only the instancer router and CTFd are bridged into the routing network.
  - Modern bind-mount checking: `safety.assert_safe` verifies that no host mounts (`HostConfig.Mounts` or `Binds`) are permitted into instance containers.
- **Docker Socket Security:** The Docker socket is never mounted into CTFd or player containers. The instancer communicates through `tecnativa/docker-socket-proxy` with minimal privileges (containers and networks CRUD only; no exec, no swarm, no volume mounts, no system reconfigurations).
- **Two-Hop HTTP Routing & Direct TCP:**
  - HTTP challenges route through the Central Router on the platform VM (Hop 1) to the Host Router (Hop 2), which streams to the local container. Hop 1 takes destination addresses strictly from `hosts.json`, enforcing destination integrity.
  - TCP challenges connect directly to the challenge host's public DNS address (e.g. `h1.inst.l3m0nctf.xyz`) on allocated ports (30000–30199).

---

## 2. Environments and Compose Overlays

The instancer provides three overlay configurations. **Never run the development overlay on a production server.**

### 2.1 Development Overlay (`deploy/compose/compose.instancer.yml`)
- **Target:** Local laptops and continuous integration testing.
- **Run command:**
  ```bash
  L3MON_OVERLAYS=instancer tools/compose.sh up -d --wait
  ```
- **Characteristics:**
  - Mounts example manifest from repository (`config/instancer/challenges.example.toml`).
  - Router binds to loopback only (`127.0.0.1:8090` and `127.0.0.1:30000-30199`).
  - Allows local unpinned images for fast offline authoring and iteration if `INSTANCER_DEV=1`.
  - Development administrator token (`PRESET_ADMIN_TOKEN`) is available for test scripts.

### 2.2 Production Overlay (`deploy/compose/compose.instancer-production.yml`)
- **Target:** Platform VM on Google Cloud Compute Engine.
- **Run command:**
  ```bash
  L3MON_ENV=production L3MON_OVERLAYS=instancer,instancer-production tools/compose.sh up -d --wait
  ```
- **Characteristics:**
  - `INSTANCER_DEV: "0"` is explicitly hardcoded and enforced.
  - Manifest is mounted read-only strictly from outside the git checkout (default `/etc/l3mon/challenges.toml:ro`).
  - All challenge images must be pinned by SHA-256 digest (`image@sha256:...`); unpinned tags are rejected on startup.
  - Instance TTL overrides by players are rejected (`L3MON_INSTANCES_TTL_OVERRIDE` is absent).
  - Router binds to public interface (`0.0.0.0:8090` and `0.0.0.0:30000-30199`).
  - `INSTANCE_DOMAIN` must be set to the production subdomain (e.g. `inst.l3m0nctf.xyz`).
  - No development administrator token (`PRESET_ADMIN_TOKEN`) is passed to any service.

### 2.3 Standalone Challenge Host (`deploy/compose/compose.challenge-host.yml`)
- **Target:** Dedicated remote challenge host VMs.
- **Run command:**
  ```bash
  docker compose -f deploy/compose/compose.challenge-host.yml up -d
  ```
- **Characteristics:**
  - Runs the agent (`INSTANCER_AGENT_MODE=1`), socket proxy, and router.
  - Exposes port 8300 (agent API) and 8080 (Hop 2 HTTP router) to the private cloud VPC only.
  - Publishes TCP ports 30000–30199 to the public internet (`0.0.0.0`).
  - Explicit container hardening (cap drop, read-only root, no-new-privileges, log rotation).

---

## 3. Configuration & Secrets

### 3.1 Secrets
**File permissions.** The agent and the router run as uid 10001 and 10002 and read their token through a bind mount, which keeps the file's own permissions: a `0600` file owned by another user is unreadable there. Token files are therefore `0644` inside a `0700` folder (the folder keeps every other user of the machine out). The manifest, which holds no secret, is `0644` as well. (Both runbooks said `0600` before 21 Nov 2026; found on the real challenge host.)

Secrets are generated using `python -m l3mon secrets generate --dir ../.secrets` and mounted via Docker secrets into `/run/secrets/`:

| Secret Name | Secret File Path | Consumed By | Purpose |
|---|---|---|---|
| `INSTANCER_TOKEN` | `.secrets/INSTANCER_TOKEN` | `ctfd`, `instancer` | Authenticates CTFd plugin requests to instancer daemon (default fallback) |
| `INSTANCER_TOKEN_<HOST>` | `/run/secrets/INSTANCER_TOKEN_<HOST>` | `ctfd` | Dedicated bearer token for remote challenge host (e.g. `INSTANCER_TOKEN_H1`) |
| `ROUTER_TOKEN` | `.secrets/ROUTER_TOKEN` | `instancer`, `router` | Authenticates router route-polling queries from instancer (default fallback) |
| `ROUTER_TOKEN_<HOST>` | `/run/secrets/ROUTER_TOKEN_<HOST>` | `router` | Dedicated route read token for remote challenge host (e.g. `ROUTER_TOKEN_H1`) |
| `FLAG_HMAC_SECRET` | `.secrets/FLAG_HMAC_SECRET` | `ctfd` | Seeds HMAC-SHA256 dynamic per-team flag derivations (never stored on challenge hosts) |

### 3.2 Environment Variables

Set in environment or `deploy/compose/generated/compose.env`:

| Variable | Default (Dev) | Default (Prod) | Purpose |
|---|---|---|---|
| `INSTANCER_DEV` | `0` | `0` (enforced) | Development switch: 1 allows unpinned images |
| `INSTANCER_AGENT_MODE` | `0` | `1` (on challenge hosts) | Enables agent mode on challenge hosts |
| `INSTANCER_HOST_NAME` | `default` | `h1`, `h2`... | Name of this challenge host in the fleet |
| `INSTANCER_MANIFEST_FILE` | `../../config/instancer/challenges.example.toml` | `/etc/l3mon/challenges.toml` | Path to challenge manifest file |
| `INSTANCE_DOMAIN` | `localhost:8090` | Required (e.g. `inst.l3m0nctf.xyz`) | Wildcard domain routing suffix |
| `INSTANCER_INSTANCE_POOL` | `10.200.0.0/16,172.16.0.0/12` | `10.200.0.0/16` | Valid network CIDR pool for instance containers |
| `INSTANCER_TCP_PORT_START` | `30000` | `30000` | Start of TCP relay port range |
| `INSTANCER_TCP_PORT_COUNT` | `200` | `200` | Number of allocated TCP relay ports |
| `INSTANCER_TCP_MAX_CONNS_PER_IP`| `60` | `60` | Max concurrent TCP connections per client IP |
| `INSTANCER_TCP_MAX_TOTAL_CONNS` | `2000` | `2000` | Global TCP relay connection ceiling |
| `INSTANCER_TCP_RATE_LIMIT_BPS` | `2097152` (2 MB/s) | `2097152` | Token bucket rate limit per client connection |
| `INSTANCER_TCP_RATE_LIMIT_BURST`| `4194304` (4 MB) | `4194304` | Token bucket burst allowance |
| `ROUTER_BIND_HOST` | `127.0.0.1` | `0.0.0.0` | The address the HOST publishes the router's ports on (a compose setting, not read by the router) |
| `ROUTER_LISTEN_ADDR` | `auto` | `auto` | The address the router PROCESS listens on. `auto` = its address on the network of the agent. A host router must never listen on `0.0.0.0` (see "What an instance can reach" below). The central router, which has a hosts file, defaults to `0.0.0.0` |
| `ROUTER_HTTP_PORT` | `8090` | `8090` | External TCP port for HTTP router |
| `POLL_INTERVAL` | `0.5` | `0.5` | Seconds between a router's polls for routes. A new instance is reachable once the host router and then the central router have polled: about one second at most |
| `INSTANCER_MAX_SLOTS` | `200` | from the sizing | The most instances this host admits at once |
| `INSTANCER_MAX_MEM_MB` | `16384` | written by `bootstrap.sh` from the machine's RAM | The most memory, in MB, that the declared limits of the running instances may add up to. A start that would pass it is refused with `no_capacity` ("Host memory capacity reached"). Before 21 Nov 2026 this figure was fixed at 16384 and nothing could set it |
| `INSTANCE_ACCESS_MODE` | `crew` | `crew` until the event, then `open` | The crew-only gate on instance addresses (central router only; see "The crew-only gate") |
| `INSTANCE_ACCESS_TEAM_DOMAIN` | | `<name>.cloudflareaccess.com` | The Cloudflare Access team name that issues the tokens |
| `INSTANCE_ACCESS_AUD` | | 64 hex characters, comma-separated for several | The application tag(s) of the crew application in Cloudflare Access |

---

## 4. Multi-Host Fleet Management (`hosts.json`)

The platform VM distributes challenge starts across multiple challenge hosts using `/etc/l3mon/hosts.json` (example in `config/instancer/hosts.example.json`):

```json
[
  {
    "name": "h1",
    "url": "http://10.200.1.10:8300",
    "router_url": "http://10.200.1.10:8080",
    "public_tcp_host": "h1.inst.l3m0nctf.xyz",
    "weight": 1.0,
    "max_slots": 150,
    "ca_cert": null
  },
  {
    "name": "h2",
    "url": "http://10.200.1.11:8300",
    "router_url": "http://10.200.1.11:8080",
    "public_tcp_host": "h2.inst.l3m0nctf.xyz",
    "weight": 1.0,
    "max_slots": 150,
    "ca_cert": null
  }
]
```

### Compose wiring for a multi-host fleet (`compose.instancer-multihost.yml`, optional)
A platform with ONE instancer (the default; every deployment so far) uses neither this overlay nor any of its files: `compose.instancer-production.yml` starts without a `hosts.json` and without per-host secrets. To switch to several hosts, add the overlay **after** the instancer overlays:

```bash
L3MON_ENV=production L3MON_OVERLAYS=instancer,instancer-production,instancer-multihost \
  L3MON_HOSTS_FILE=/etc/l3mon/hosts.json \
  L3MON_INSTANCER_TOKENS_DIR=/etc/l3mon/host-secrets/instancer \
  L3MON_ROUTER_TOKENS_DIR=/etc/l3mon/host-secrets/router \
  tools/compose.sh up -d --wait
```
It also works in development (`L3MON_OVERLAYS=instancer,instancer-multihost`, with a hosts file and two folders of your own).

- `hosts.json` is mounted read-only into `ctfd` (`L3MON_HOSTS_PATH`) and into the `router` (`ROUTER_HOSTS_PATH`).
- One folder of `INSTANCER_TOKEN_<NAME>` files (one per listed host, name in capitals) is mounted read-only into `ctfd` only; one folder of `ROUTER_TOKEN_<NAME>` files into the router only. Any number of hosts needs **no edit of a compose file**: a new host is a line in `hosts.json` and two files.
- A listed host without its token is never fed the platform token: CTFd logs an ERROR at start naming the missing variable and does not use the host; the central router refuses to start and names it.
- The host name `default` is reserved for the implicit single-host mode and is refused in `hosts.json`.

> [!IMPORTANT]
> **Domain matching.** The challenge host kit's `INSTANCE_DOMAIN` **must equal the platform's `INSTANCE_DOMAIN`**. The central router forwards `Host: <id>.<domain>` unchanged to the host's router; with different domains the host answers HTTP 404.

> [!IMPORTANT]
> **The host-down mark is shared.** When a host fails a probe, the mark "down for 30 seconds" is kept in CTFd's cache (Redis), so every CTFd worker agrees. A player's list also probes the hosts of the listed instances (through the 5-second capacity cache), so a dead host is found without waiting for anyone to start an instance, and the player sees `error` rather than `running`.

### Scheduling & Failover Invariants:
1. **5-Second Capacity Cache:** `GET /v1/capacity` queries from each host are cached for 5 seconds to reduce inter-machine overhead.
2. **Optimistic Local Counter:** When a host is selected for a start, its local in-memory `slots_used` is immediately incremented (`_optimistic_slots += 1`). This eliminates the "thundering herd" problem where rapid concurrent starts pile onto one host during the 5s cache window.
3. **30-Second Down Penalty:** If a host fails to respond (timeout or connection refused), the scheduler marks it `DOWN` for 30 seconds. No new starts are routed to it during this window.
4. **Instance Stickiness:** An instance remains on its assigned host for all query, renew, and stop operations.
5. **Down Host Instance Status:** If an instance's host is down, the player API returns `state="error"`, never `"running"`.
6. **Hop 1 Destination Integrity:** The central router looks up the instance's host and forwards HTTP traffic strictly to that host's configured `router_url`. Untrusted route targets from the agent are completely ignored.

### The platform VM in a fleet: `compose.instancer-central.yml` (the production way with a challenge VM)

With the challenge instances on a separate VM (`deploy/challenge-host/`), the platform VM must not run an instancer or a Docker-socket proxy: the overlays above (`instancer`, `instancer-production`, `instancer-multihost`) all start both, because they are the one-machine and development forms. `instancer-central` is the overlay for the fleet and **replaces those three**:

```bash
L3MON_ENV=production L3MON_OVERLAYS=identity,ops,instancer-central \
  INSTANCE_DOMAIN=l3m0nctf.xyz \
  L3MON_HOSTS_FILE=/etc/l3mon/hosts.json \
  L3MON_INSTANCER_TOKENS_DIR=/etc/l3mon/host-secrets/instancer \
  L3MON_ROUTER_TOKENS_DIR=/etc/l3mon/host-secrets/router \
  tools/compose.sh up -d --wait
```

It runs only the **central router** (hop 1, no published port), gives CTFd the hosts file, the folder of `INSTANCER_TOKEN_<NAME>` files and the flag secret, gives the router the hosts file and the folder of `ROUTER_TOKEN_<NAME>` files (never the other's), and gives **nginx** one extra server block for the instances' web addresses (`deploy/nginx/instances/instances.conf`). Without this overlay nginx has no such block and nothing changes. A challenge instance is then reached at `https://<32 hex>.l3m0nctf.xyz/` on the **normal HTTPS port 443** (Cloudflare's wildcard record `*.l3m0nctf.xyz`, proxied; its free certificate covers exactly one level), through the same Cloudflare client-certificate check as the platform. The block answers only a name that starts with 32 hex characters and a dot; the router then checks the rest of the name against `INSTANCE_DOMAIN` and answers 404 for any other domain. A challenge that tries to set a cookie for the whole zone gets it rewritten to its own host name (otherwise it could shadow the platform's session cookie). `tools/tests/test_instancer_central.py` renders the overlay, checks that no Docker socket is mounted anywhere, that the router publishes nothing, that each token folder goes only where it belongs, and runs `nginx -t` on the real production configuration with and without the block.

**Security note: instance pages are on a sibling name of the platform (the owner's choice of 19 Nov 2026), so they are the same *site* as the platform though not the same *origin*.** What protects the platform: (1) its session cookie is `__Host-session` (`Secure; HttpOnly; Path=/`, no `Domain`; checked live on 19 Nov with `curl -sI https://play.l3m0nctf.xyz/login`), and a browser refuses to let any other origin or a `Domain` cookie overwrite, shadow or delete a `__Host-` cookie, so a challenge page cannot fix or replace a player's session; (2) the Same-Origin Policy keeps a challenge page from reading anything of the platform (different host name), and the platform sends no cross-origin permission headers; (3) every state-changing request to CTFd carries a CSRF token that lives in the session, which a challenge page cannot read; (4) nginx rewrites any `Domain` attribute a challenge sends to its own host name. What is **not** closed: a challenge page's own JavaScript can still write many cookies for the whole zone (`document.cookie` with `Domain=l3m0nctf.xyz`), enough to make the platform refuse a request from that visitor's browser (a request header that is too large) until the cookies are cleared. The victim must open that player's instance address, which is a random 32-character name. The complete fix is a second registrable domain for the instances (about two dollars a year; `INSTANCE_DOMAIN` on the platform and the hosts, a wildcard record and a certificate for it; **no code change**, the nginx block matches the 32 hex characters whatever the domain is). Decide before the event; for the test days the same domain is fine.

Rollback is removing `instancer-central` from `L3MON_OVERLAYS` and recreating: the player experience (the board, flags, scoring) does not depend on it.

---

## 4a. What an instance can reach (and the proof)

A player who gets code execution inside their own instance (that is the point of a pwn challenge) must not be able to reach anything else. `services/instancer/tools/real_host_proof.py` checks this, and the limits, on a REAL challenge host (not a fake Docker): authentication, bad input, the web flow, the container as Docker created it (read back from the daemon), isolation from inside an instance, lifecycle, TCP, resource limits, crash recovery, agent and router restarts, concurrency, kill-all, and leftovers. `services/instancer/tools/real_host_prepare.sh` builds the sample challenges (including a deliberately misbehaving one) and writes the test manifest. Run it before every event and after every change to the instancer, the router or the host set-up:

```bash
bash services/instancer/tools/real_host_prepare.sh
python3 services/instancer/tools/real_host_proof.py --agent http://<private ip>:8300 --router http://<private ip>:8080 --tcp-host <private ip>
python3 services/instancer/tools/real_host_proof.py --only expiry,capacity,leaks      # the long ones: the TTL, and filling the host to its memory ceiling
```

What the first run on the real host (21 Nov 2026) found, and what was done:

| Found | Why it matters | Fixed |
|---|---|---|
| From inside team A's instance the router answered on its address on A's network: its HTTP port (a request with another instance's id as `Host` returned that instance's page) and all 200 TCP relay ports (team B's TCP instance answered) | The host router is attached to the private network of every instance, so a player with code execution in any instance could attack every TCP instance (the 200 ports are guessable) and any instance whose id they knew | The router listens only on its address on the trusted network (`ROUTER_LISTEN_ADDR=auto`, `local_address_towards` in `router.py`); proved again on the host |
| 200 `docker-proxy` processes held 1731 MiB (8.5 MiB each) of an 8 GB machine before any challenge ran | 22 per cent of the machine, gone | `"userland-proxy": false` in `daemon.json` (the kernel forwards the ports); `bootstrap.sh` writes it; memory used fell from 1967 MiB to 727 MiB |
| The agent ran on fixed ceilings (200 instances, 16384 MB) that nobody could set | It would have admitted instances well past the machine's memory | `INSTANCER_MAX_SLOTS` / `INSTANCER_MAX_MEM_MB`; admission refuses at the memory ceiling; `bootstrap.sh` derives it from the machine's RAM |
| A container that died (a crash, the memory limit) stayed "running" until its TTL | The player saw a live instance that answered nothing and could only stop it and wait the full 60 s cooldown | A liveness sweep every 5 s tears it down; the team may start again after 10 s |
| The relay listened, and web requests were routed, before the challenge had opened its port | A player who connected in the first second got an instant close (TCP) or a 502 (web) | The router keeps trying for 20 s after the start of an instance (`READY_GRACE_SECONDS`) |
| A request to an instance that had just stopped hung for the 30 s idle timeout before the 404 | | One short connect timeout of 3 s (`BACKEND_CONNECT_TIMEOUT`) for an instance that is not new |
| Routes were polled every 2 s per hop | A new instance answered 404 or "connection refused" for up to 4 s | `POLL_INTERVAL` 0.5 |
| The runbooks said token and manifest files must be `0600` | The containers run as uid 10001 and 10002 and cannot read such a file | `0644` inside a `0700` folder |

Known limits, not changed: the router does not carry WebSocket upgrades (`Upgrade` is a hop-by-hop header and is dropped), so a web challenge that needs a WebSocket does not work; the agent accepts a request body of any size from the platform (it is private and needs the token).

---

## 4b. The crew-only gate (instance addresses during a test round)

**Why.** Owner's instruction of 21 Nov 2026, in capitals: while challenges are being tested, only the people the admins approved on `crew.l3m0nctf.xyz` may reach them. Instance addresses are `<32 hex>.l3m0nctf.xyz`, a name that the crew host's Cloudflare Access rule does not cover, so they need a gate of their own.

**How it works.** Cloudflare Access (the service that guards the crew host) puts a signed token (a JWT, RS256) on every request it lets through. The central router checks that token itself, before it looks up the instance (`services/instancer/router/access_gate.py`), so the gate does not depend on the Cloudflare dashboard being set up right:

- Mode `crew` (the default) lets a request through only with a valid token for one of the crew application's tags (`INSTANCE_ACCESS_AUD`) of the team `INSTANCE_ACCESS_TEAM_DOMAIN`. Everybody else gets the same `403` for every name, so an outsider cannot even tell whether an instance exists.
- It **fails closed**: with the team name or the tag missing, or the signing keys unreachable and none known, it refuses everything. It never falls back to open.
- RS256 only (not `none`, not HS256 with the public key as the secret); keys under 2048 bits are not trusted; expired, not-yet-valid, wrong-issuer and wrong-application tokens are refused. The keys come from `https://<team>.cloudflareaccess.com/cdn-cgi/access/certs`, are cached for an hour, are refreshed when a token names an unknown key (at most once a minute) and keep working for up to a day if Cloudflare cannot be reached.
- The token and the `CF_` cookies are removed before a request is forwarded to a challenge: a challenge must never be able to replay a crew member's token against the crew host.
- Metrics on the router: `instancer_router_access_gate_open`, `instancer_router_access_gate_configured`, `instancer_router_access_refusals_total{reason}`.
- `l3mon deploy check` BLOCKS a deployment that has the central router on, mode `crew`, and no team name or tag, and says "TODO" when the mode is `open`.

**The Cloudflare side (owner, once).** In Cloudflare Zero Trust, Access, Applications: add the hostname `*.l3m0nctf.xyz` to the crew application (so the policy is literally the crew policy), and add a Bypass application for each name that must stay public (`play`, `www`, the apex), because the most specific hostname wins. Then copy the crew application's Audience tag into `INSTANCE_ACCESS_AUD`. Add the wildcard DNS record last. Until then nothing is reachable at all, which is the strict state.

**TCP instances** are not behind Cloudflare. For a test round the firewall rule `l3mon-chal-allow-tcp-relays` (TCP 30000-30199 from the whole internet) is DISABLED, and TCP instances are tried from inside the cloud network or through an IAP tunnel. Enable the rule again for the event.

**For the event:** set `INSTANCE_ACCESS_MODE=open` in `production.env` and restart the router (`tools/compose.sh up -d router`). Nothing else changes.

---

## 4c. Sizing a challenge host

The figure that matters is real memory per instance, which must be MEASURED for each real challenge, because it is far below the declared limit for a small web app and far above it for a heavy one. `pack_test.py` and `real_host_proof.py --only capacity` measure it. Measured on `l3mon-chal` (4 vCPU, 8 GB, 21 Nov 2026) with the repository's sample web challenge (Python `http.server`, declared limit 64 MB):

- 90 instances held 1991 MB of host memory in all: **22 MB each** (including the router, the agent and one Docker network per instance), against 64 MB declared.
- The agent admitted exactly the 90 that fit its ceiling of 5772 MB (64 MB declared each) and refused the next with `no_capacity`, a reason and a retry time.
- A single start takes about 0.6 s; 24 simultaneous starts took 8.8 s (about 2.7 starts a second); stopping 90 instances took 28 s.

`INSTANCER_MAX_MEM_MB` is checked against the DECLARED limits (the safe direction), so set `mem_mb` in the manifest to what the challenge really needs with some room, not to a round number.

---

## 5. Author Challenge Image Workflow (GHCR Integration)

Authors build and deliver containerized challenges following Decision 4 (GitHub Container Registry):

1. **Automated CI Build & Push:**
   - Challenges are authored in the private challenges repository.
   - GitHub Actions workflow builds and pushes images to GHCR (`ghcr.io/sivaahari/...`).
   - Authors never hold direct write credentials to the registry; CI enforces image immutability.
2. **Private Packages & Read-Only Host PATs:**
   - Every package in GHCR is marked **private**.
   - Challenge hosts use a root-only, read-only fine-grained personal access token (`read:packages`) stored at `/etc/l3mon/secrets/ghcr_token` (mode 0600).
3. **Zero Baked Flags Rule:**
   - **No flag is ever baked into a Docker image layer.**
   - If a host or image is compromised, no participant flags are leaked. Flags are supplied exclusively at runtime via the `FLAG` environment variable derived dynamically by CTFd.
4. **Offline Fallback (`docker load`):**
   - For offline rehearsals or air-gapped operations, images can be loaded from `.tar` archives via `docker load`. The manifest must specify `image = "<repository>@sha256:<digest>"` referencing the locally tagged digest.

---

## 6. Manifest Format Specification & Field Limits

The manifest (`challenges.toml`) defines challenge specs loaded by the instancer:

```toml
[[challenge]]
slug = "pwn-lemon-kernel"
image = "ghcr.io/sivaahari/pwn-kernel@sha256:7f83b1657ff1fc53b92dc18148a1d65dfc2d4b1fa3d677284addd200126d9069"
protocol = "tcp"          # Allowed: "http" or "tcp"
port = 1337               # Target internal port inside container (1..65535)
cpus = 1.0                # CPU quota (0.1 .. 4.0)
mem_mb = 128              # Memory limit in MB (32 .. 2048)
pids = 64                 # Process limit (16 .. 256)
ttl_minutes = 45          # Base instance lifetime (5 .. 120)
max_per_team = 1          # Active instances per team (default 1)
max_body_bytes = 10485760 # Max HTTP request size in bytes (HTTP only, default 10MB)
response_timeout_seconds = 120 # HTTP backend response timeout
```

---

## 7. Operational Runbook

### 7.1 Emergency "Kill All" Instances
Terminates all active ephemeral instances across all configured hosts without touching core platform containers:
```bash
# Call platform admin kill endpoint
curl -X POST -H "Authorization: Bearer <CREW_ADMIN_TOKEN>" \
  -H "Content-Type: application/json" \
  -d '{}' https://ctfd.l3m0nctf.xyz/api/v1/l3mon/admin/instances/kill
```

### 7.2 Token Rotation
Tokens can be rotated without interruption:
1. Update `/etc/l3mon/secrets/INSTANCER_TOKEN` and `ROUTER_TOKEN` on the challenge host.
2. Restart the challenge host agent and router.
3. Update `INSTANCER_TOKEN_<HOST>` and `ROUTER_TOKEN_<HOST>` in the platform configuration and reload CTFd.
