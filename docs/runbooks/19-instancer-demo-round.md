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

## D. One raw-TCP check (optional; opens the public rule for your address only)

The public rule `l3mon-chal-allow-tcp-relays` is disabled, so from your computer a TCP instance cannot be reached. To try it, give the rule your address for an hour, enable it, and put it back afterwards. This changes the cloud firewall: do it only on purpose. (These two lines are ordinary gcloud and were **not** run in the 22 Nov round: the round connected from inside the host's own network. Check the rule afterwards with `bash tools/ops/demo-round.sh status`.)

```bash
gcloud --configuration=l3mon-new compute firewall-rules update l3mon-chal-allow-tcp-relays --source-ranges="<your public IP>/32" --no-disabled
gcloud --configuration=l3mon-new compute firewall-rules update l3mon-chal-allow-tcp-relays --source-ranges="0.0.0.0/0" --disabled
```

Connect to the host's **public IP address** (`34.180.12.8`) and the port the page shows, not to the name: the name `h1.inst.l3m0nctf.xyz` points at Cloudflare through the wildcard record, which does not carry raw TCP. For the event that name needs its own **DNS-only (grey cloud)** record to the host's address. The sample answers one line of greeting and, after you send `flag` and a newline, one flag line. The second command above is not optional: it puts the rule back to what it was.

## E. End (about 3 minutes)

```bash
bash tools/ops/demo-round.sh run cleanup
bash tools/ops/demo-round.sh run counts
bash tools/ops/demo-round.sh instance-gate crew
bash tools/ops/gate.sh open
bash tools/ops/demo-round.sh vm-stop
bash tools/ops/demo-round.sh status
```

`instance-gate crew` puts the router back to its resting mode (`INSTANCE_ACCESS_MODE=crew`).

`cleanup` ends `cleanup done; notifications now: 0`. `counts` must show `challenges 0 ... channels 0 programmes 0`, `test users: 0` and `instance rows: 0 | live ones: 0`. `gate.sh open` ends `GATE IS OPEN: the channels answer players. (confirmed from outside ...)`. `status` must show the challenge VM `TERMINATED` and the TCP rule `DISABLED: True`. Your own player account and studio are never touched by `cleanup`: it removes only what is named `test-inst-`, the channel `crew-test` and the five sample challenges (their solves with them).

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
| the instance link did not open when clicked (fixed 22 Nov) | the agent gave an `http://` address; Cloudflare serves 443 only | `INSTANCE_URL_SCHEME` (empty = https for a real domain) |

## What the wrapper does (to repeat it by hand)

`run <command>` copies `services/instancer/tools/platform_demo_round.py` and `services/instancer/tools/run_platform_demo.sh` to `/tmp` on the platform VM over the IAP tunnel, strips Windows line ends, and runs `bash /tmp/run_platform_demo.sh <command> /tmp/platform_demo_round.py` there. That script passes the Python text to `l3mon-run /opt/venv/bin/python -c` inside the application container (its folders are read-only, so nothing is copied into it). `vm-start` and `vm-stop` are `gcloud compute instances start|stop l3mon-chal`, and `vm-start` then waits until the agent and the host router are up and the agent answers its capacity call.
