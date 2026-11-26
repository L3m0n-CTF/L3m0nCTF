# Runbook 19: the instancer demo round (sample challenges, through the platform)

Written 22 Nov 2026, from the round that was run that day (`docs/deploy/verification-log.md`, 22 Nov). Use it after any change to the instancer, the router, the platform's instance code or a challenge host, and as a rehearsal before the event. It takes about 30 minutes by hand, 10 of them waiting. It needs the platform VM, the challenge VM and the player gate, and it **closes the player gate** for its length.

## What it touches, and what a player sees

| What | Effect |
|---|---|
| The player gate (`tools/ops/gate.sh close`) | Players are told "Not on air yet" and cannot see the demo or start an instance. Sign-in and registration keep working. It is opened again at the end. |
| A channel `crew-test` with five programmes | `demo-web`, `demo-web2`, `demo-web3`, `demo-web-short` (a 1-minute life) and `demo-tcp`, made through the platform's own plan and release code. Worth 0 TRP. The "New on air" announcement is switched off for the run: no notification is made, nothing reaches the bell. |
| Hidden test studios `test-inst-NN` (only for the automatic checks) | Hidden from every scoreboard and the CTFtime feed. Removed by `cleanup`. |
| The challenge VM | Started, and stopped at the end. Its public TCP rule stays **disabled** unless you open it for one raw-TCP check (below). |
| The activity ledger | Keeps the test accounts' sign-in and instance lines for good (it cannot be edited, by design). |

## Once, on your laptop

You need Git Bash (the commands are bash), `gcloud` signed in, and the Google Cloud project id. **PowerShell's own `bash` is the Linux subsystem and has no gcloud**: in PowerShell run Git Bash by its path. The two forms below are the same command.

```bash
export CLOUDSDK_CORE_PROJECT="<your Google Cloud project id>"
bash tools/ops/demo-round.sh status
```

```powershell
$env:CLOUDSDK_CORE_PROJECT = "<your Google Cloud project id>"
& "C:\Program Files\Git\bin\bash.exe" tools/ops/demo-round.sh status
```

Below, commands are written for Git Bash; in PowerShell put `& "C:\Program Files\Git\bin\bash.exe"` in front of `bash`. `status` shows both machines, whether the public TCP rule is disabled, and what the player gate says. Start from: the platform `RUNNING`, the rule disabled, the gate open.

## A. Prepare (about 3 minutes)

```bash
bash tools/ops/demo-round.sh vm-start
bash tools/ops/gate.sh close --yes
bash tools/ops/demo-round.sh instance-gate open
bash tools/ops/demo-round.sh run setup_programme
bash tools/ops/demo-round.sh run counts
```

**Do not skip `instance-gate open`.** The central router rests in `crew` mode (nobody may open an instance's address until Cloudflare Access is set up), and in that mode the page starts an instance but the address it shows answers `Forbidden: these addresses are open to the organising crew only for now`. That was exactly what stopped the first manual round on 22 Nov. `open` lets in anybody who has the address (a random 32-character name that exists only while the instance runs); that is acceptable only for sample challenges, with the player gate closed, for the length of the round. It restarts only the central router, so players are not affected, and prints `INSTANCE_ACCESS_MODE=open`. `status` always shows the current mode.

`vm-start` waits for the host's stack and prints its capacity (`slots_used` 0). `gate.sh close` ends with `GATE IS CLOSED ... (confirmed from outside ...)`; if it does not say so, stop. `setup_programme` ends with `released: channel crew-test and 5 programmes` and `notifications before / after (must be equal)`: the two numbers must be equal. `counts` shows `challenges 5 ... programmes 5`.

## B. By hand in a browser (about 15 minutes)

You need a player account in a studio (yours; do not use the crew account, it has no studio). While the gate is closed the channel pages answer only on the crew address and to the organisers' addresses: sign in at `https://crew.l3m0nctf.xyz` (Cloudflare Access asks for an approved email first), then your player account, then **Channels, Crew test**. A programme that carries an instance has a "Your instance" panel.

| # | You do | You should see |
|---|---|---|
| 1 | Demo web, **Start** | "Starting" for 1 to 2 seconds, then running, an `https://<32 characters>.l3m0nctf.xyz` address and a countdown of about 15 minutes |
| 2 | Open the address, then add `/flag` | the sample page; a flag that starts `L3m0n{` |
| 3 | Paste the flag into the programme | "Correct" (0 TRP) |
| 4 | Open the address on a phone on mobile data | the same page |
| 5 | **Renew** | refused, always: an instance lives 15 minutes and cannot be extended (the owner's rule of 23 Nov 2026). The page still shows the button and says "NOT YET. This instance cannot be renewed yet." (its wording was written for a renewal that opens later); hiding the button needs a change in the page, not on the server |
| 6 | **Stop**, then Start at once | stopped, the address dies within about 10 s; an immediate restart is refused for about 60 s |
| 7 | Start Demo web, web two, web three, then try Demo web short | the fourth is refused: the studio already runs the most instances it may |
| 8 | Demo web short, **Start**, wait a minute | expired; the address is dead; **Start works again at once** (an instance that ran out its time leaves no restart cooldown, a deliberate Stop does: 60 s) |
| 9 | Demo tcp, **Start** | an address `h1.inst.l3m0nctf.xyz` and a port from 30000 to 30199 (see D) |

Anything that differs: write down what you pressed, the exact words on the page and the time, and take a screenshot.

## C. The automatic checks (about 5 minutes; they use hidden test studios)

```bash
bash tools/ops/demo-round.sh run setup
bash tools/ops/demo-round.sh run functional
bash tools/ops/demo-round.sh run burst
```

Three small commands help around them: `run probe` makes one signed-in call as test studio 1 and prints the answer (nothing changes), `run reset` stops every instance of the test studios through the platform and removes their solves (use it to rerun `functional`, after waiting out the 60 s restart cool-down), and `run expected_tcp` prints a 10-character hash prefix of the flag the platform expects for studio 1's TCP programme, to compare with the hash of what the instance served.

`functional` prints one `PASS` or `FAIL` line per check and ends `RESULT: 24 passed, 0 failed` (it waits about 80 s for a 1-minute instance to expire). `burst` starts 24 studios at the same moment and ends `RESULT: 4 passed, 0 failed`; its `BURST:` line gives the times (22 Nov: 11.2 s for the whole burst, median 7.5 s, longest 11.1 s). A `FAIL` line says what it saw.

### Host down and back, and an instance gone from the host

Needs `setup` and `functional` first (so that studio 2 holds an instance). Each command must **begin with a word, never a path**, or Git Bash rewrites it.

```bash
gcloud --configuration=l3mon-new compute ssh l3mon-chal --zone=asia-south1-a --tunnel-through-iap --quiet --command 'sudo docker stop l3mon-challenge-host-agent-1'
bash tools/ops/demo-round.sh run hostdown_during
gcloud --configuration=l3mon-new compute ssh l3mon-chal --zone=asia-south1-a --tunnel-through-iap --quiet --command 'sudo docker start l3mon-challenge-host-agent-1'
bash tools/ops/demo-round.sh run hostdown_after
```

Expect: while the agent is stopped a start by a studio with no instance is refused at once ("No instancer host has capacity", retry in 30 s) and a studio's running instance is shown as `error`; after the start a fresh start works within seconds and the instance is `running` again (the platform keeps a "host down" mark for 30 s, so `hostdown_after` waits). Add `--project` to the `gcloud` lines if `CLOUDSDK_CORE_PROJECT` is not set. For an instance that disappears from the host: `run gone` prints an instance id of studio 2; on the challenge VM find its container with `sudo docker ps -a --format '{{.ID}} {{.Names}} {{.Labels}}' | grep <instance id>` and remove it with `sudo docker rm -f <container id>`; then `run after_gone` expects the platform to stop showing it as running at once.

## D. A raw-TCP instance (Zero Cool, Jarvis, the sample `demo-tcp`): what must be true BEFORE `ncat` can work

The page gives a TCP instance a name and a port: `h1.inst.l3m0nctf.xyz` and a number from 30000 to 30199. For a player's connection to reach the challenge host, **two things outside the platform must be true**. The first raw-TCP test from a real computer (25 Nov 2026, Zero Cool) failed because **neither was**: the audit log of the Google project shows nobody touched the firewall rule between 24 Nov and the end of the session, and `h1.inst.l3m0nctf.xyz` resolved to the same two Cloudflare addresses as `play.l3m0nctf.xyz`. (The 22 Nov round had connected from inside the host's own network, which needs neither, so it never showed.)

**1. DNS: the name must point at the host, with the orange cloud OFF.** Cloudflare, `l3m0nctf.xyz`, DNS, Records, Add record: Type `A`, Name `h1.inst`, IPv4 address `34.180.12.8`, Proxy status **DNS only** (the grey cloud), TTL Auto, Save. The wildcard `*` record stays as it is (orange): it serves the random instance names of web challenges, and a record with an exact name always wins over it. Cloudflare's orange cloud carries web traffic only; a raw-TCP connection sent through it is never answered. Check from your computer (PowerShell), it must print `34.180.12.8` and **not** `104.21.x.x` or `172.67.x.x`:

```powershell
Resolve-DnsName h1.inst.l3m0nctf.xyz -Server 1.1.1.1 -Type A
```

(Your computer may keep the old answer for up to 5 minutes: `ipconfig /flushdns`.)

**Which name?** The page shows the name in the platform's `/etc/l3mon/hosts.json` (`public_tcp_host`), read once when the platform app starts: today `h1.inst.l3m0nctf.xyz`. Any DNS-only record that points at the host's address reaches the host: the host does not look at the name. On 26 Nov 2026 the owner created **`chall.instance.l3m0nctf.xyz` -> `34.180.12.8` (DNS only)** instead of `h1.inst`; it resolves correctly (checked against 1.1.1.1, 8.8.8.8 and his own resolver). While `hosts.json` still says `h1.inst`, the page shows `h1.inst.l3m0nctf.xyz` (which still points at Cloudflare and will not connect): **connect to `chall.instance.l3m0nctf.xyz` and the PORT the page shows**. Before the event pick one, so that players can copy the address from the page: add a second DNS-only record `h1.inst` -> `34.180.12.8` (no change to the platform), or change `public_tcp_host` to `chall.instance.l3m0nctf.xyz` in `/etc/l3mon/hosts.json` on the platform VM and restart the platform app (a production change: the owner's yes, a quiet moment).

**2. The Google firewall rule `l3mon-chal-allow-tcp-relays` must be ON for the connecting address.** It rests OFF, with source `0.0.0.0/0`. For a test, switch it on for your own public address only, and switch it off afterwards (`status` shows its state; neither command touches a machine):

```bash
bash tools/ops/demo-round.sh tcp-open <your public IPv4 address>
bash tools/ops/demo-round.sh tcp-close
```

Your public address is the `ip=` line of `https://1.1.1.1/cdn-cgi/trace` (open it in a browser on the same network you will test from; on a phone hotspot or at home it is a different address than on the campus network). `tcp-open` refuses anything that is not one public address, so it can never open the rule to everybody. **For the event** the rule must be ON for everybody (`--source-ranges=0.0.0.0/0 --no-disabled`) when players may start TCP instances: that is a decision for the day, not a thing this tool does.

**Connect.** Windows has no `nc`; use `ncat` (it comes with Nmap, already installed on the owner's computer) with the host and port the page shows:

```powershell
ncat chall.instance.l3m0nctf.xyz <port>
Test-NetConnection chall.instance.l3m0nctf.xyz -Port <port>
```

(`chall.instance...` is the record the owner made; use `h1.inst...` once that record exists too and the page shows it.)

`Test-NetConnection` only says whether the port answers (`TcpTestSucceeded : True`). The sample `demo-tcp` answers one line of greeting and, after you send `flag` and a newline, one flag line. On the host the same name is **not** needed: from the challenge VM itself the check uses the private address, which is why that check passed on 22 Nov without any of the above.

If both are true and it still fails: try a phone hotspot (a campus or home network can block outbound connections to high ports; unverified), start a fresh instance (the relay port is chosen at Start), and read the agent's log on the challenge VM (`sudo docker logs --tail 40 l3mon-challenge-host-agent-1`) and the router's (`l3mon-challenge-host-router-1`).

## E. End (about 3 minutes)

```bash
bash tools/ops/demo-round.sh run cleanup
bash tools/ops/demo-round.sh run counts
bash tools/ops/demo-round.sh instance-gate crew
bash tools/ops/demo-round.sh tcp-close
bash tools/ops/gate.sh open
bash tools/ops/demo-round.sh vm-stop
bash tools/ops/demo-round.sh status
```

`instance-gate crew` puts the router back to its resting mode (`INSTANCE_ACCESS_MODE=crew`). `tcp-close` is safe to run even when `tcp-open` never was (it sets the rule to its resting state, OFF with source `0.0.0.0/0`); `gate.sh open` only if you closed the player gate.

`cleanup` ends `cleanup done; notifications now: 0`. `counts` must show `challenges 0 ... channels 0 programmes 0`, `test users: 0` and `instance rows: 0 | live ones: 0`. `gate.sh open` ends `GATE IS OPEN: the channels answer players. (confirmed from outside ...)`. `status` must show the challenge VM `TERMINATED` and the TCP rule `DISABLED: True`. Your own player account and studio are never touched by `cleanup`: it removes only what is named `test-inst-`, the channel `crew-test` and the five sample challenges (their solves with them).

## F. Your own challenge (what the crew does for each live challenge until a challenge sync exists)

Written 25 Nov 2026 and **run once through on the real stack with a sample challenge** (start through the platform 201, address through Cloudflare serving the studio's own flag after 1.4 s, flag accepted, channel bonus paid and taken away again by `cleanup`). It needs the challenge VM and the instance gate open (A above), and it does **not** need the player gate closed, but read the two warnings first.

**Two warnings, because `play` is open and the event window is live:**

1. **Players can see what is on air.** Anything on the channel `crew-test` is on the Channels page of every signed-in player until `cleanup`. `own_setup` therefore puts only a **placeholder** on the platform (a challenge named "Crew test <Slug>", 0 TRP, no text, no files); the real challenge is only its instance. A player who finds that tile and presses Start would get your real challenge's instance, so keep the time short and **close the player gate (`tools/ops/gate.sh close --yes`, open it again at the end) when the challenge is a real one you do not want seen before the event.** The sample challenges need no gate.
2. **A studio that solves every programme of a channel is paid the channel bonus (100 TRP by default), and your studio is on the scoreboard.** `cleanup` takes that bonus away again (since 25 Nov), but until then your studio shows it. Solve your challenge in a studio you do not mind, or leave one programme of the channel unsolved (run `setup_programme` first: the five samples share the channel and you solve only yours).

```bash
# 1. the host: build your folder (a Dockerfile at its top), pin it, write its stanza, restart the agent   (about 1 minute; the first build pulls base images)
bash tools/ops/demo-round.sh add-challenge /d/work/challenge-intake-2026-11-21/adapted/delos delos http 8000 128
# 2. the platform: put it on air on the channel crew-test, silently (no "New on air" notice)
bash tools/ops/demo-round.sh run own_setup delos
```

`add-challenge <folder> <slug> <http|tcp> <port> [mem_mb]`: the **port** is the one your challenge listens on inside its container (the `EXPOSE` or the one in the server's code); `mem_mb` is the limit (64 to 128 for most; give a challenge that writes much to `/tmp` more). Write the folder with forward slashes. It prints `ADDED: <slug> ...` when the agent has read the new manifest; it first checks the new manifest with the agent's own loader, so a typo cannot take the host down, and keeps the old one as `/etc/l3mon/challenges.toml.before-add`. Adding the same slug again replaces it (use that after changing the folder). The adaptation rules of docs/deploy/instancer.md section 4d apply: if Start answers "could not be started just now", read the agent's log on the challenge VM (`sudo docker logs l3mon-challenge-host-agent-1`) and the container's own output.

Then, as in B: sign in as your player, **Channels, Crew test**, the programme "Crew test <Slug>": Start, open the address, solve it with your reference solution, paste the flag. The flag the platform expects is **derived per studio and passed to the container as `FLAG`**: your challenge must serve exactly that value at the end of its solution (nothing is baked into the image). For a raw-TCP challenge see D first: the DNS-only record `h1.inst` must exist and `tcp-open <your IP>` must have been run; you then connect to the name and port the page shows.

End as in E: `run cleanup` removes the placeholder, the channel, every instance and any bonus. `cleanup` does **not** remove the challenge from the host's manifest or the image from its registry: they are harmless and are replaced when you add the same slug again.

## Things that went wrong on 22 Nov, and what each meant

| Seen | Cause | What to do |
|---|---|---|
| The instance address shows `Forbidden: these addresses are open to the organising crew only for now` (22 Nov, the first manual round) | the central router is in `crew` mode and nobody switched it to `open` for the round; the instance itself was started and was fine | `bash tools/ops/demo-round.sh instance-gate open`, then reload the address (a running instance needs no restart) |
| `The required property [project] is not currently set` | the gcloud configuration `l3mon-new` has no project | set `CLOUDSDK_CORE_PROJECT` as above |
| `bash: ... C:/Users/.../run_demo.sh: No such file` | a command that began with a path was rewritten by Git Bash | begin the command with a word (`bash /tmp/x`, not `/tmp/x`); do not set `MSYS_NO_PATHCONV` (it breaks gcloud itself) |
| a fetch of an instance address answers 403 from a script | Cloudflare turns away Python's default client name | send a browser's name; `curl` and browsers are fine |
| an unsafe call answers 403 although signed in | CTFd checks its CSRF header only for a JSON request | send `{}` as the body (the page already does) |
| `host back: the studio's earlier instance is still error` right after the start | the platform keeps its "host down" mark for 30 s | wait 30 s; the check polls for it |
| an address answers for about 9 s after Stop | the container's 5-second stop grace and the routers' two polling cycles | normal |
| `ncat` / `nc` to a raw-TCP instance hangs or times out (25 Nov, Zero Cool) | two causes at once: `h1.inst.l3m0nctf.xyz` pointed at Cloudflare (the wildcard record), which does not carry raw TCP, and the Google rule `l3mon-chal-allow-tcp-relays` was OFF | the DNS-only record `h1.inst` and `tcp-open <your IP>` (section D); `tcp-close` afterwards |
| `nc` is not recognised in PowerShell | Windows has no `nc` | use `ncat` (Nmap) or `Test-NetConnection` (section D) |
| the instance link did not open when clicked (fixed 22 Nov) | the agent gave an `http://` address; Cloudflare serves 443 only | `INSTANCE_URL_SCHEME` (empty = https for a real domain) |

## What the wrapper does (to repeat it by hand)

`run <command>` copies `services/instancer/tools/platform_demo_round.py` and `services/instancer/tools/run_platform_demo.sh` to `/tmp` on the platform VM over the IAP tunnel, strips Windows line ends, and runs `bash /tmp/run_platform_demo.sh <command> /tmp/platform_demo_round.py` there. That script passes the Python text to `l3mon-run /opt/venv/bin/python -c` inside the application container (its folders are read-only, so nothing is copied into it). `vm-start` and `vm-stop` are `gcloud compute instances start|stop l3mon-chal`, and `vm-start` then waits until the agent and the host router are up and the agent answers its capacity call.
