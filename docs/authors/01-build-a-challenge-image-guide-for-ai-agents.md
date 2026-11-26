# Guide 1 for AI agents: help an author turn a challenge into a Docker image the platform can run

Written 27 Nov 2026 for the L3m0nCTF 2026 platform. **Give this whole file to the AI agent that sits next to the author.** The second file, `02-put-a-docker-challenge-on-air-commands.md`, is what comes after this one (it puts the finished image on the platform).

---

## 0. The rules for you, the AI agent (read these first, they beat everything below)

1. **You GUIDE. You do not execute.** Do not run, and do not offer to run, a build, a `docker` command, a `gcloud` command, a deploy or a script yourself, even if you have a shell tool. You write the files into the conversation (or save them in the author's own folder if the author asks), you give the exact command, you say what the output must look like, and the **author runs it** and pastes the result back. The author owns every action. If the author says "just do it", answer that this project's rule is that the person who owns the challenge presses every button, and give the command again.
2. **Never ask for, accept, print or repeat a real flag, a password, a token or a key.** The flags of the contest are made by the platform at runtime. In every test use a random test value that the author generates on their own machine (section 7 shows how). If the author pastes something that looks like a real secret, tell them to remove it and rotate it.
3. **Never bake a flag into an image**, not in a file, not in an `ENV` line, not in a build argument, not in a comment. The flag arrives in the environment variable `FLAG` when an instance starts and nowhere else. A baked flag is the one mistake that cannot be forgiven: anybody who can pull the image reads it.
4. **Ask before you assume.** If you do not know the challenge's language, how it is started, which port it listens on, or what files it writes, ask the author (section 2). Do not invent.
5. **One change at a time, and prove it.** After every change, give the command that checks it and wait for the output.
6. **Say plainly when something cannot be done** (section 4 lists what the platform does not support). Offer the honest alternatives rather than a workaround that weakens the isolation.
7. **Do not touch the platform, the cloud, the repository or other people's challenges.** Your job ends with a folder that builds, runs and passes the checks in section 8, plus the hand-over note of section 9.

---

## 1. What the platform does with an image (so that you know why the rules exist)

- Each studio (team) that presses **Start** on a live challenge gets **its own container** of the image, on a separate challenge machine. It lives **15 minutes**, cannot be renewed, and is then destroyed. The team may start a new one (a deliberate Stop causes a 60 second wait; an expiry does not). **One instance per team and challenge at a time.**
- A **web** challenge is reached at `https://<32 random characters>.l3m0nctf.xyz`. Cloudflare and the platform's router take the HTTPS and pass plain HTTP to the container's port. The container never sees HTTPS.
- A **raw TCP** challenge (pwn, a service you `nc` into) is reached at `h1.inst.l3m0nctf.xyz` and a port from 30000 to 30199 chosen at Start. The platform relays the raw bytes to the container's port.
- The container gets the team's **flag in the environment variable `FLAG`**. The flag is derived from the team and the challenge (`L3m0n{` plus 32 lower-case hex characters plus `}`): **different for every team, the same flag again if the same team restarts**. The platform compares what the player pastes with that derived value. The author never sets a flag for a live challenge.
- The container is **hardened on purpose**, so that a player who gets code execution inside it can do no harm outside it: it runs as a normal user, the root file system is read-only, there are no Linux capabilities, no way out to the internet and no way to the other instances.

---

## 2. Questions to ask the author first

Ask them one by one and write the answers down as a table at the top of your working notes.

| # | Ask | Why |
|---|---|---|
| 1 | The name of the challenge, and its **slug**: lower-case letters, digits, `-` and `_`, start with a letter or digit, at most 48 characters (for example `delos`, `zero-cool`) | The slug is the challenge's name on the challenge machine and on the platform's board; it must be identical in both places |
| 2 | **Web (http) or raw TCP?** | Decides the template (section 5) and how it is reached |
| 3 | Which **language and framework**, and how is it started today (`docker compose up`? `python app.py`? `socat`?) | You will write the Dockerfile from this |
| 4 | **Which port** does it listen on inside the container? (Exactly one port.) | Goes into the manifest as `port` |
| 5 | Where does the code **read the flag from** today (a file `flag.txt`? an environment variable? a database row?) | Section 3 |
| 6 | Does it **write any file** while it runs (logs, uploads, a database, a cache, a flag file at start-up)? Where? | The root file system is read-only; only `/tmp` is writable (section 4) |
| 7 | Does it need **another container** (a database, a cache, a second service), **outbound internet**, a **WebSocket**, `root` or a special capability? | These are not supported (section 4) |
| 8 | How much **memory and CPU** does it really use under a solver? (Section 8 shows how to measure it.) | Sets `mem_mb` and checks the CPU quota |
| 9 | Is there a **reference solution** (a solver script) that prints the flag? | The proof that the adapted image still works (section 8) |
| 10 | Is the author on Windows, macOS (Apple chip) or Linux? | Command syntax; Apple-chip machines must build for `linux/amd64` |

---

## 3. The flag rules, with the patterns that work

The flag is **only** in the environment variable `FLAG`, and only while the instance runs.

**Pattern A: the program reads `FLAG` itself.** Best for web challenges: `os.environ["FLAG"]` (Python), `process.env.FLAG` (Node), `getenv("FLAG")` (PHP, C). Read it when the request needs it, or once at start. Nothing to write to disk.

**Pattern B: the program reads a file such as `flag.txt`** (typical for pwn). The root file system is read-only, so an entry-point script writes the file **under `/tmp`** at start, and the Dockerfile makes the path the program expects a link to it **at build time** (the build runs as root, so it can create links):

```dockerfile
RUN ln -s /tmp/flag.txt /app/flag.txt        # or /flag.txt if the binary opens an absolute path
WORKDIR /tmp                                 # for a binary that opens "flag.txt" relative to its working directory
```

```sh
#!/bin/sh
# entrypoint.sh: write the team's flag under /tmp, then hand over to the real service
set -eu
: "${FLAG:?FLAG is not set}"
umask 0277                                   # the file is readable only by the user the instance runs as
printf '%s\n' "$FLAG" > /tmp/flag.txt
unset FLAG                                   # the service's own environment no longer holds it
exec "$@"
```

**The silent failure to avoid.** An old entry-point that does `echo "$FLAG" > /app/flag.txt 2>/dev/null` fails on the read-only file system **without any message** because of the `2>/dev/null`, and the instance then serves whatever flag was baked in at build time. Never hide the error: keep `set -eu` and no `2>/dev/null` around the flag write. This exact mistake cost a round of testing on 21 Nov (Zero Cool and Jarvis Override).

**Placeholders.** If the program needs a file to exist while you build or test, put a clearly fake text in it, such as `PLACEHOLDER_NOT_A_FLAG`. It must not look like `L3m0n{...}`.

---

## 4. What the platform supports and what it does not (hard limits)

The values below are enforced by the challenge machine's own manifest loader (`services/instancer/manifest.py`); a value outside them makes the loader refuse the whole manifest.

| Thing | Rule |
|---|---|
| Containers per instance | **One.** No compose file, no sidecar database. Merge the parts into one container (SQLite or in-memory data instead of a database server), or ask the organisers about a shared service outside the instancer |
| User | The instance runs as **uid 10001** whatever the Dockerfile says. Files must be **readable** (and programs **executable**) by "other". Do not rely on a home directory (`HOME` may not exist; set `ENV HOME=/tmp`) |
| Root file system | **Read-only.** Nothing may be written outside `/tmp` |
| `/tmp` | RAM-backed, **64 MB**, and it **counts against the instance's memory limit** |
| Capabilities | **None** (`cap_drop: ALL`, `no-new-privileges`). No `NET_ADMIN`, no `iptables`, no raw sockets, no `ptrace` between processes, no mounts, no `chroot`, no `su`, no changing kernel settings (so the ASLR setting of the host cannot be changed by the image) |
| Network | **No way out**: no DNS, no internet, no other container. Do not install packages or fetch anything at run time. A web page may still tell the **player's browser** to load a script from a CDN, but bundle everything if you can |
| Processes | at most **256** (`pids`), the default is 128. A fork bomb is stopped by this limit |
| Memory | `mem_mb` up to **1024**; most challenges need 64 to 128 |
| CPU | `cpus` up to **2.0**, the default is 0.5; the test tool uses 0.25. A pwn challenge may be sensitive to the quota: check it (section 8) |
| Lifetime | **15 minutes**, whatever `ttl_minutes` says (the host's ceiling wins) |
| HTTP | plain HTTP inside; request body at most 1 MB by default (10 MB at most); a response may take up to 120 seconds by default. **WebSockets are not supported** (the router drops `Upgrade`) |
| Port | exactly one, listening on **`0.0.0.0`** (not `127.0.0.1`) inside the container |
| CPU architecture | the challenge machine is **x86-64**: build for `linux/amd64` |
| The image | must be **pinned by digest** in the manifest (`name@sha256:<64 hex>`); never `:latest` |

Not supported, by design (tell the author honestly, and do not hunt for a workaround): two or more containers, outbound network (for example DNS to the player's own server), a root process, `iptables`, WebSockets.

---

## 5. Templates

Use these as starting points and **change only what the challenge needs**. Pin base images to a version.

### 5.1 Web, Python (Flask)

`Dockerfile`:

```dockerfile
FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 HOME=/tmp
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . /app
# any file the code writes at start becomes a link into /tmp (built as root, so this works):
# RUN ln -s /tmp/app.db /app/app.db
USER 10001
EXPOSE 8000
CMD ["gunicorn", "--bind", "0.0.0.0:8000", "--workers", "2", "--threads", "4", "--access-logfile", "-", "app:app"]
```

The code reads `os.environ["FLAG"]`. Python bytecode is not written (`PYTHONDONTWRITEBYTECODE`) because the file system is read-only.

### 5.2 Web, Node

```dockerfile
FROM node:20-slim
ENV NODE_ENV=production HOME=/tmp
WORKDIR /app
COPY package*.json ./
RUN npm ci --omit=dev
COPY . .
USER 10001
EXPOSE 8000
CMD ["node", "server.js"]
```

Make the server listen on `0.0.0.0:8000`. Do not let npm write a cache at run time (`HOME=/tmp` keeps it in the RAM disk).

### 5.3 Web, PHP

Apache normally needs root and writable directories, which this platform does not give. Use PHP's own server:

```dockerfile
FROM php:8.3-cli
WORKDIR /app
COPY . /app
USER 10001
EXPOSE 8000
CMD ["php", "-S", "0.0.0.0:8000", "-t", "/app"]
```

If sessions or uploads are needed, point `session.save_path` and the upload folder at `/tmp`.

### 5.4 Raw TCP (pwn, or any service you reach with `nc`)

`Dockerfile` (compile in one stage, run in a small one):

```dockerfile
FROM ubuntu:22.04 AS build
RUN apt-get update && apt-get install -y --no-install-recommends build-essential && rm -rf /var/lib/apt/lists/*
COPY chall.c /src/chall.c
# the author's own compile flags go here (this line is only an example of a classic stack challenge)
RUN gcc -o /src/chall /src/chall.c -fno-stack-protector -no-pie

FROM ubuntu:22.04
RUN apt-get update && apt-get install -y --no-install-recommends socat && rm -rf /var/lib/apt/lists/*
COPY --from=build /src/chall /app/chall
COPY entrypoint.sh /app/entrypoint.sh
RUN chmod 0755 /app/chall /app/entrypoint.sh && ln -s /tmp/flag.txt /app/flag.txt
WORKDIR /tmp
USER 10001
EXPOSE 1337
ENTRYPOINT ["/app/entrypoint.sh"]
CMD ["socat", "TCP-LISTEN:1337,reuseaddr,fork", "EXEC:/app/chall,stderr"]
```

`entrypoint.sh` is the one in section 3 (Pattern B). Notes: `fork` makes one process per connection (the process limit protects the machine); add `pty,echo=0` or `rawer` to the `EXEC:` options only if the binary needs a terminal; wrap a service that can hang with `timeout 120` if the author wants connections to end by themselves. The binary's protections (PIE, canary, RELRO) are the author's design, set in the compile line; the host's ASLR setting is whatever the host has and cannot be changed by the image.

### 5.5 Anything else that talks TCP (a crypto oracle, a custom protocol)

Same as 5.4 with the author's interpreter instead of a compiled binary (for example `socat TCP-LISTEN:1337,reuseaddr,fork EXEC:"python3 /app/server.py",stderr`). The program talks on standard input and output; `socat` does the network part.

### 5.6 When no image is needed

A challenge that is only a **file to download** (reverse engineering binary, forensics image, crypto text) needs **no image and no instance**. It is uploaded as a normal challenge with its file and a static flag. By the owner's decision of 26 Nov 2026 such files are the **same for every team, with one shared flag**. Do not build an image for it.

---

## 6. The Dockerfile hygiene list (check every one)

- [ ] `FROM` names a version (`python:3.12-slim`), never `latest`.
- [ ] No flag, key or password anywhere in the folder. Search the folder before building (section 7, step 2).
- [ ] The program listens on `0.0.0.0` and on **one** port that matches `EXPOSE` and the manifest.
- [ ] Every file the program writes while it runs is under `/tmp`, or is a link into `/tmp` made at build time.
- [ ] Files are readable by "other"; binaries are `chmod 0755`.
- [ ] `USER 10001` is set, so that the local test behaves like the real run.
- [ ] No `curl | sh`, no `ADD http://...`, no unexplained downloads at build time. (The organisers read the Dockerfile before it goes on the machine: anything surprising delays it.)
- [ ] No compose-only features (`depends_on`, volumes, a second service) are needed.
- [ ] A `.dockerignore` keeps `.git`, the solver, the write-up and local test data out of the image.
- [ ] Logs go to standard output or error, not to a file.

---

## 7. How the author tests it locally (you give the commands, the author runs them)

Write each command in the form the author's computer uses. Windows users run PowerShell with Docker Desktop; the examples are written so that they work in both PowerShell and bash unless a note says otherwise.

**Step 1. Build** (the author is in the challenge folder, the one with the Dockerfile):

```bash
docker build --platform linux/amd64 -t chal:test .
```

Expected: it ends without an error. If it fails, ask for the **last 20 lines** and fix that, nothing else.

**Step 2. Search for a baked flag.** A folder or image that matches means a flag is in it:

```bash
docker run --rm --entrypoint sh chal:test -c "grep -rIl 'L3m0n{' /app /srv /opt /home /etc /tmp 2>/dev/null || echo clean"
```

Expected: `clean`. (Windows without `sh` in the image: use an image that has it, or ask the author to run `docker history chal:test` and `docker save chal:test | findstr /c:"L3m0n{"`.) Also have the author search the source folder itself for `L3m0n{`.

**Step 3. Make two random test flags** (never real ones). bash:

```bash
FLAG1="L3m0n{$(openssl rand -hex 16)}"; FLAG2="L3m0n{$(openssl rand -hex 16)}"; echo "$FLAG1"; echo "$FLAG2"
```

PowerShell:

```powershell
$FLAG1 = "L3m0n{" + (-join (1..32 | ForEach-Object { '{0:x}' -f (Get-Random -Maximum 16) })) + "}"; $FLAG2 = "L3m0n{" + (-join (1..32 | ForEach-Object { '{0:x}' -f (Get-Random -Maximum 16) })) + "}"; $FLAG1; $FLAG2
```

**Step 4. Run it the way the platform runs it** (this is the important part: read-only, no capabilities, uid 10001, limits). Web, port 8000 (change the two `8000`s to the author's port; for TCP the same command works and the author connects with `ncat`/`nc` to `localhost <port>`):

```bash
docker run --rm --name chal -p 8000:8000 --read-only --tmpfs /tmp:rw,size=64m --cap-drop ALL --security-opt no-new-privileges --user 10001:10001 --memory 128m --cpus 0.5 --pids-limit 64 -e FLAG="$FLAG1" chal:test
```

PowerShell: the same line, with `-e "FLAG=$FLAG1"` in place of `-e FLAG="$FLAG1"`.

Expected: the program starts and stays up; the log shows no `Read-only file system`, `Permission denied` or `cannot create` message. If it dies at once, the program wrote somewhere that is not `/tmp`: find the path in the message and apply section 3 or the `ln -s` pattern.

**Step 5. Run the author's reference solver** against `http://localhost:8000` (or the TCP port). It must print exactly `$FLAG1`. Stop the container (`docker stop chal`), start it again with `$FLAG2`, run the solver again: it must print `$FLAG2` and **not** `$FLAG1`. This proves that the flag really comes from `FLAG`, and that two teams get two different flags.

**Step 6. Check there is no way out** (the instance has no network). In a second window, while it runs:

```bash
docker exec chal sh -c "getent hosts example.com || echo no-dns"
```

(Many slim images have no `getent`; then skip it. The challenge must work with the network that the platform gives, which is none, so also run Step 4 once with `--network none` in place of `-p 8000:8000` and confirm the container stays up.)

---

## 8. Measure, then set the limits

While the solver runs, the author reads the real use:

```bash
docker stats --no-stream chal
```

- `mem_mb` for the manifest = the highest memory seen **plus headroom** (for example 40 MB seen, give 128; a challenge that writes a lot to `/tmp` needs more, because `/tmp` counts). Do not give round, generous numbers: the machine admits instances by the **declared** memory, so a bigger number means fewer teams at once.
- A **pwn** challenge: repeat Step 4 with `--cpus 0.25` and run the solver. If the exploit becomes unreliable at 0.25, tell the organisers the CPU quota it needs.
- Time a solve: if the solve takes more than a few minutes of the 15 an instance lives, the challenge is too slow for this platform; say so.

---

## 9. The hand-over note (what the author gives to the organisers)

When Steps 1 to 6 pass, help the author fill this in, as plain text:

```
Challenge name:
Slug:                      (lower-case, digits, - and _; at most 48)
Protocol:                  http | tcp
Container port:
mem_mb:                    (measured: ... MB seen, ... MB given)
cpus needed:               (0.25 / 0.5 / ...)
Folder:                    (the folder with the Dockerfile at its top)
Solver:                    (path; prints the flag when run against an instance)
Checks passed:             build / no baked flag / read-only run / two flags / --network none / solver
Flag lives in:             env FLAG, read by ... / written by entrypoint.sh to /tmp/flag.txt
Description for players:   (text, no spoilers)
Points / difficulty:       (50..125 style number, warmup | easy | medium | hard | insane)
Hints (if any):
```

Then tell the author: **next, give this note and the folder to the person who puts challenges on air, who follows `02-put-a-docker-challenge-on-air-commands.md`.**

---

## 10. Things that went wrong before, and what each meant

| Seen | Cause | Fix |
|---|---|---|
| The instance starts but the page or service shows the **wrong flag** (a placeholder or an old one) | The entry-point wrote the flag to a read-only path with `2>/dev/null` | Write under `/tmp`, link at build time, keep `set -eu`, remove the `2>/dev/null` (section 3) |
| The container exits at once with `Read-only file system` or `Permission denied` | The program writes next to its own code (a database, a log, a generated file) | Make that path a link into `/tmp` at build time |
| `could not be started just now` on the platform | The container died at start, or the manifest is wrong | The organiser reads the agent's log and the container's output (the second guide, section B) |
| The web page loads but a script or font is missing | The page loads it from a CDN that the **player's** network blocks, or the server fetches it | Bundle it into the image |
| The pwn solver works locally but not on the platform | CPU quota (0.25), a different libc, or ASLR | Test with `--cpus 0.25`; build and run in the same base image; do not depend on the host's kernel settings |
| Port refused | The program listens on `127.0.0.1`, or on another port than the manifest says | Listen on `0.0.0.0` and match `port` |
| The instance is killed for memory | `/tmp` filled (it counts) or `mem_mb` is too low | Raise `mem_mb` or write less |
| A WebSocket does not connect | Not supported | Use plain requests (polling) |
| It works on the author's Apple-chip laptop and fails on the platform | Built for `arm64` | `docker build --platform linux/amd64` |

---

## 11. A starter message the author can paste to you

> You are helping me prepare a CTF challenge for the L3m0nCTF platform. Read the whole guide I gave you first. You only guide: you write files and commands in the chat and I run everything myself. Never ask for a real flag or secret. Start with the questions in section 2, one at a time, then adapt my challenge following sections 3 to 9. My challenge folder is: (paste the file tree and the Dockerfile or compose file, **without any real flag**).
