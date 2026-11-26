# Guide 2: put a Docker challenge (web, pwn, anything that needs an image) on the platform: the exact commands

Written 27 Nov 2026 for the L3m0nCTF 2026 platform. It follows `01-build-a-challenge-image-guide-for-ai-agents.md`: **start only with a folder that passed every check of Guide 1** and has its hand-over note (section 9 of Guide 1).

**Who runs what.** The person who puts challenges on air (called "you" below, the owner or a crew member) runs **every** command and presses **every** button. If an AI agent helps you, it only explains and checks what you paste back. It must not run the commands, must not open the cloud console for you and must not ask for passwords or flags.

---

## 0. Rules for an AI agent that is helping with this file

1. **Guide only.** Show one step at a time: the command, what a good answer looks like, and what to do if it is different. The human runs it and pastes the output. Never run a command yourself, even if you have a shell, a browser or the cloud tools.
2. **Stop at every "STOP" line** and wait for the human's word. Nothing in this file may be done "to save time" in one go.
3. **Cloud actions need the owner's yes at that moment**: starting or stopping a machine, opening the firewall, changing the router mode, releasing a challenge to players. Ask "do you want to do this now?" before you show the command.
4. **Never ask the human to paste or type a flag, a password, a token or a key.** If the output of a command contains one, tell them to hide it.
5. **If anything differs from "Expected", stop and diagnose.** Do not improvise another command. Use the troubleshooting table at the end.
6. **Releasing to players is the one step that cannot be taken back quietly** (every player gets a "New on air" notice). Say so before section C4.

---

## A. Once per computer

You need: Git Bash, the Google Cloud command `gcloud` signed in to the project, Docker Desktop, and a copy of this repository on the **release tag** (`git describe --tags` shows the release; ask the lead which). The commands are written for Git Bash. **In PowerShell, `bash` is the Linux subsystem and has no `gcloud`**, so there put the full path in front, from the repository folder:

```powershell
$env:CLOUDSDK_CORE_PROJECT = "<the Google Cloud project id: the lead knows it>"
& "C:\Program Files\Git\bin\bash.exe" tools/ops/demo-round.sh status
```

The same in Git Bash:

```bash
export CLOUDSDK_CORE_PROJECT="<the Google Cloud project id>"
bash tools/ops/demo-round.sh status
```

Everywhere below `bash tools/ops/...` means: in PowerShell write `& "C:\Program Files\Git\bin\bash.exe" tools/ops/...`. Write folder paths with **forward slashes** and the drive as `/d/work/x` (not `D:\work\x`).

Expected from `status`: the two machines (`l3mon-app` RUNNING; `l3mon-chal` RUNNING or TERMINATED), the public TCP rule (DISABLED at rest), and what the player gate says. **STOP if it prints an error about the project**: the variable above is not set.

---

## B. The host side: put the image on the challenge machine

Do this per challenge. The slug, protocol, port and mem are in the hand-over note.

### B1. Look at the Dockerfile before it goes anywhere (mandatory for anybody's challenge but your own)

The build runs **on the challenge machine** (the test-host route, which is the one proven end to end on 21 to 26 Nov), using that machine's own network. That is acceptable for a Dockerfile you have read and not for one you have not. Open it and check:

- [ ] `FROM` is an ordinary public image with a version (`python:3.12-slim`, `ubuntu:22.04`, `node:20-slim`).
- [ ] There is no `curl ... | sh`, no `wget` of a script, no `ADD http...`, no `--privileged`-style trick, no mention of the Docker socket.
- [ ] There is no flag, key or password in the folder: `grep -rIn "L3m0n{" <folder>` prints nothing (in PowerShell: `Select-String -Path "<folder>\*" -Pattern "L3m0n\{" -Recurse`).
- [ ] The Dockerfile has `EXPOSE <port>` equal to the port in the hand-over note.

**STOP** if any box is not ticked: send it back to the author.

### B2. Start the challenge machine

It rests stopped (it costs money while running). Ask yourself whether you want it on now.

```bash
bash tools/ops/demo-round.sh vm-start
```

Expected: it waits for the machine's stack and prints its capacity (`slots_used` 0). It takes about a minute.

### B3. Ship the folder, build it, pin it, register it

```bash
bash tools/ops/demo-round.sh add-challenge /d/work/<your-folder> <slug> <http|tcp> <port> <mem_mb>
```

Example for a web challenge: `bash tools/ops/demo-round.sh add-challenge /d/work/delos delos http 8000 128`.
Example for a TCP challenge: `bash tools/ops/demo-round.sh add-challenge /d/work/zero_cool zero-cool tcp 1337 64`.

What it does, in order: packs the folder (without `.git` and `node_modules`), copies it to the challenge machine, builds the image **there**, pushes it to that machine's own local registry and reads back its digest (`image@sha256:...`), writes a block for the challenge into the machine's manifest `/etc/l3mon/challenges.toml` (CPU 0.25, 64 processes, 15 minutes, one instance per team), checks the **new** manifest with the agent's own loader **before** it restarts anything, and restarts the agent (running instances are not touched).

Expected, in the last lines:

```
   pinned: localhost:5000/owner/<slug>@sha256:...
   the agent's loader accepts it: N challenges
ADDED: <slug> (<protocol>, container port <port>, <mem> MB, 15 minutes); the agent is back and has read the manifest
```

Running it again with the same slug **replaces** the challenge (use that after changing the folder). The old manifest is kept as `/etc/l3mon/challenges.toml.before-add`.

**If it does not end with `ADDED:`**, copy the last 15 lines to the helper, and read the log on the machine:

```bash
gcloud --configuration=l3mon-new compute ssh l3mon-chal --zone=asia-south1-a --tunnel-through-iap --command "sudo docker logs --tail 40 l3mon-challenge-host-agent-1"
```

Typical causes: a build error (the last lines of the build are printed), a wrong slug (lower-case letters, digits, `-`, `_`), or the manifest loader refusing a value.

### B4. A TCP challenge needs two things outside the platform (web challenges need neither)

1. **DNS**: the name `h1.inst.l3m0nctf.xyz` must point at the challenge machine with the orange cloud **off** (Cloudflare, DNS, an `A` record named `h1.inst`, DNS only). It is made once. Check it (read-only, changes nothing):

   ```bash
   bash tools/ops/demo-round.sh tcp-check
   ```

   Expected: lines starting `OK`. **STOP at a `PROBLEM` line** and read it: it says where the name points instead.

2. **The Google firewall rule `l3mon-chal-allow-tcp-relays`** (ports 30000 to 30199 of the challenge machine). It rests **OFF**. For **your own test**, switch it on for your own public address only, and off afterwards:

   ```bash
   bash tools/ops/demo-round.sh tcp-open <your public IPv4 address>
   bash tools/ops/demo-round.sh tcp-close
   ```

   Your address is the `ip=` line of `https://1.1.1.1/cdn-cgi/trace` opened on the network you will test from. `tcp-open` refuses anything that is not exactly one public address, so it cannot open the rule to everybody.

   **On the event day** the rule must be **on for everybody** while players may start TCP instances. That is the owner's decision for the day; the command is:

   ```bash
   gcloud --configuration=l3mon-new compute firewall-rules update l3mon-chal-allow-tcp-relays --source-ranges=0.0.0.0/0 --no-disabled
   ```

   and afterwards `bash tools/ops/demo-round.sh tcp-close`. A player on a network that blocks ports 30000 to 30199 (the Amrita Wi-Fi does) cannot reach a TCP challenge whatever we do; a phone hotspot is the way round, and the rules page should say so.

### B5. Test it before any player can see it (the proven rehearsal path)

This puts a **placeholder** on a hidden test channel `crew-test`, silently (no "New on air" notice), so that you can play your own challenge in your own studio. Read the two warnings in `docs/runbooks/19-instancer-demo-round.md` section F first (players can see what is on that channel, and a studio that solves every programme of a channel is paid the channel bonus).

```bash
bash tools/ops/demo-round.sh instance-gate open
bash tools/ops/demo-round.sh run own_setup <slug>
```

`instance-gate open` lets the router answer an instance's address (without it the address answers `Forbidden: these addresses are open to the organising crew only for now`). Expected from `own_setup`: `released: channel crew-test and 1 programmes` and equal notification counts.

Then, in a browser, **as a player** (your own studio; the crew account has no studio and cannot send a flag): open the platform, **Channels, Crew test**, the programme **"Crew test <Slug>"**, press **Start**, open the address, run the author's solver or solve it by hand, and paste the flag. Expected: the address answers within a few seconds, the flag you get is accepted ("Correct"). For TCP, connect with `ncat h1.inst.l3m0nctf.xyz <port>` (`nc` does not exist in PowerShell); from a network that blocks the port use `bash tools/ops/demo-round.sh tcp-tunnel <port>` and `ncat localhost <port>`.

When done:

```bash
bash tools/ops/demo-round.sh run cleanup
bash tools/ops/demo-round.sh instance-gate crew
bash tools/ops/demo-round.sh tcp-close
```

`cleanup` removes the placeholder, the test channel, every instance and any bonus it caused; it does **not** remove the challenge from the host's manifest (harmless). `instance-gate crew` puts the router back to its resting mode (see section D).

---

## C. The platform side: the real challenge, as players will see it

These steps are done in the **browser** by a crew member signed in at `https://crew.l3m0nctf.xyz` (Cloudflare Access asks for an approved email first). The host side (section B) must already say `ADDED`.

### C1. Create the challenge in CTFd

`/admin` , **Challenges** , **New**.

| Field | Value |
|---|---|
| Name | the challenge's name |
| Category | its category (Web, Pwn, ...) |
| Message (description) | the text for players, **with the files or the story**; no spoilers. **Do not write a flag or an address here**: the address comes from the **Start** button |
| Value | its points (an ordinary number; the board shows TRP) |
| Type | `standard` |
| State | **hidden** (leave it hidden; the release page controls what is on air) |

Create it, then open it: **Flags** , **new flag**, type **`l3mon_dynamic`**, and leave the content **empty**. (This is the type that derives each team's own flag; it matches what the container receives in `FLAG`.) Save. **Do not add a normal static flag.** STOP: if the type `l3mon_dynamic` is not in the list, tell the lead (the platform is not on a release that has it).

### C2. Place it on a channel, as a live server

`/admin/l3mon/release` , **Set up the board** , **A challenge on a channel**:

| Field | Value |
|---|---|
| Challenge | the one you created |
| Channel | its channel |
| Programme number, place | leave as filled in |
| **Short name (slug)** | **exactly the slug you gave to `add-challenge`** (this is what ties the board to the host's manifest) |
| Difficulty | `warmup`, `easy`, `medium`, `hard` or `insane` |
| **Delivery** | **A live server for each studio** (`live_single`) |

Save. It starts **off air**. Without that delivery the page shows no **Start** button; with a short name that differs from the host's slug the Start answers "could not be started just now".

The same can be done by a plan sent as a call (the third box on that page takes a whole plan): a programme entry is `{"challenge_id": <id>, "channel": "<channel slug>", "cell": <n>, "number": <n>, "slug": "<the host slug>", "delivery": "live_single", "difficulty": "<word>"}`.

### C3. Check it as the crew (nothing on air yet)

`/admin/l3mon/release` shows the programme as **withheld**. The crew see a preview of what is on air; players see nothing. **STOP** if the row is missing or shows an error.

### C4. Release it to players (point of no easy return)

**STOP: ask the owner now.** Pressing **Release now** on a channel and on a programme sends a **"New on air" notice to every real player**. Do it only when the event, or this drop, is meant to start. Order: the **channel** first, then each **programme**. Nothing a player can see exists until both are released and the broadcast window (the event's start and end time) is open.

The way back for one programme: **Take off the board** on its row (players stop seeing it; the challenge stays in CTFd).

---

## D. The event-day checklist for live challenges

Run these on the morning, in this order, and write down what each said.

1. **The machine is up and has room:** `bash tools/ops/demo-round.sh vm-start` (if it was stopped), then `bash tools/ops/demo-round.sh status`. The capacity the agent prints must exceed what you expect at the peak (the number of studios that may hold an instance at once).
2. **The challenge machine is on exactly the release tag** (not on a hand-edited copy): the lead runs `chal-from-tag.sh` for that tag (it also recomputes the capacity settings).
3. **Every live challenge has its block in the manifest** (`ADDED` seen in B3) and its programme has delivery **A live server for each studio** with the matching short name.
4. **The instance router's mode.** It rests in `crew` mode: every instance address answers `Forbidden: these addresses are open to the organising crew only for now`, **including for players**. For players to open their instance one of two things must be true, and **it is the owner's decision**: (a) Cloudflare Access is set up for the crew application and `INSTANCE_ACCESS_AUD` holds its application tag (the platform's deploy check line "instance addresses" says BLOCKED until then), or (b) the router is switched to `open` (anybody who has an instance's random 32-character address can open it, which is what a player needs):

   ```bash
   bash tools/ops/demo-round.sh instance-gate open
   ```

   `status` always shows the current mode.
5. **TCP challenges only:** the DNS check passes (`bash tools/ops/demo-round.sh tcp-check`) and the firewall rule is **on for everybody** (section B4).
6. **A last look as a player** in a real studio on a phone network: Start, open, solve, flag accepted, for one web and one TCP challenge.
7. **After the event:** `bash tools/ops/demo-round.sh tcp-close`, stop the challenge machine (`bash tools/ops/demo-round.sh vm-stop`: every instance ends), and set the platform's size back down (the lead has the exact steps).

---

## E. Taking a challenge back

- **Off the board:** `/admin/l3mon/release` , **Take off the board** on its row.
- **Off the host:** instances already running keep running until they expire (15 minutes). To remove its block from the manifest, on the challenge machine edit `/etc/l3mon/challenges.toml` (the previous version is `challenges.toml.before-add`) and restart the agent; ask the lead rather than edit it by hand during the event.
- **Everything at once in an emergency:** the crew's "kill all instances" (`docs/runbooks/08-kill-instances.md`).

---

## F. When something does not work

| You see | It means | Do |
|---|---|---|
| `The required property [project] is not currently set` | the project variable is missing | set `CLOUDSDK_CORE_PROJECT` (section A) |
| `bash: ... No such file` for a path you typed | Git Bash rewrote a path that began a command | begin the command with a word (`bash ...`); write folders as `/d/work/x` |
| `add-challenge` stops with a build error | a Dockerfile problem | read the last lines it printed; fix the folder; run it again (same slug replaces) |
| `add-challenge` says `the local registry does not answer` | the challenge machine's stack is not up | `vm-start`, wait a minute, run again |
| Start answers **could not be started just now** | the container died at start, or the short name differs from the slug, or the delivery is not `live_single` | read the agent's log (section B3) and compare the short name with the slug |
| The address shows `Forbidden: these addresses are open to the organising crew only for now` | router in `crew` mode | section D4 |
| The instance starts but the flag shown/accepted is wrong | the flag type is not `l3mon_dynamic`, or the image serves a baked flag | check C1; run Guide 1 section 7 steps 2 and 5 again |
| TCP: `ncat` hangs or is refused | DNS still points at Cloudflare, the firewall rule is off, or your network blocks the port | `tcp-check`; `tcp-open <your IP>`; test the network with `portquiz.net` as in runbook 19 section D; use `tcp-tunnel` |
| `nc` is not recognised in PowerShell | Windows has no `nc` | use `ncat` (comes with Nmap) or `Test-NetConnection <host> -Port <port>` |
| Instance link answers 403 from a script | Cloudflare turns away Python's default client name | send a browser's name; `curl` and browsers are fine |
| After a Stop, **Start** is refused for a minute | the 60 second cooldown after a deliberate Stop | wait; an expiry has no cooldown |
| The page still shows a **Renew** button | the page predates the 15-minute rule; renewal is refused by the server | ignore it; renewals are not allowed |

More detail: `docs/deploy/instancer.md` (the manifest, section 4d for the adaptation rules) and `docs/runbooks/19-instancer-demo-round.md` (the rehearsal).
