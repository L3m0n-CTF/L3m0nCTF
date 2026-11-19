# Runbook 15: trying a challenge, and the crew's rehearsal

For the crew. Read section 1 first: it explains why a challenge that was only made in CTFd never shows on the board.

## 1. How a challenge gets on the board

The board draws **channels**, and each channel holds **programmes**. A programme is a challenge placed on a channel. Four things must all be true before a player sees a challenge:

| # | What | Where the crew does it |
|---|------|------------------------|
| 1 | The challenge exists, with its flag | CTFd, `/admin`, Challenges, New |
| 2 | It is **on a channel** (a channel exists, and the challenge is placed on it) | `/admin/l3mon/release`, "Set up the board" |
| 3 | The channel and the programme are **on air** | the same page: the *Release now* buttons |
| 4 | **The broadcast window is open** (the platform's clock is between the start and the end of the event) | the settings (`start` and `end`), see section 3 |

Until number 4, **players see nothing on the board** (it says "Not on air yet"). **The crew see what is on air as a preview**, so a board can be set up and looked at days before the start; the crew have no studio, so they cannot send a flag. To play a challenge through as a player (flag, hints, TRP), the window has to be open: that is the rehearsal of section 3. Until number 2 the board has no row to draw the challenge in. CTFd's own "visible" setting on a challenge is not number 3: the platform keeps CTFd's state in step with the plan, and CTFd refuses to show a challenge that is not on air.

**Setting up with the forms** (no code): open `/admin/l3mon/release`.

1. *A channel*: give it a name (for example "Test Street"). It starts off air.
2. *A challenge on a channel*: choose the challenge, choose the channel. The programme number, the place on the picture and the short name are filled in; change them only if you want to. It starts off air.
3. Press *Release now* on the channel, then on the programme. Players now see it (if the window is open).

**The way back.** *Take off the board* (on a programme's row) puts a challenge on no channel again; players stop seeing it, and the challenge stays in CTFd. *Remove this empty channel* removes a channel with nothing on it. Both ask first, and both leave a line in the crew's trail.

The third box takes a whole plan at once (the file the author kit makes). The same can be sent as a call: `PUT /api/v1/l3mon/admin/programmes` (see `docs/deploy/local.md`).

**Try as a player.** Crew accounts have no studio, so they cannot send a flag: a flag is recorded for a studio. The page now says so in plain words (before 16 November it showed CTFd's bare "You don't have the permission"). To play, register an ordinary account, set up a studio, and sign in with that. Name test accounts `test-` and something (`test-anna`), so that they can be found and removed. To only **check** that a flag is right, open the challenge in the admin area (`/admin/challenges`, the challenge, *Preview*): it says correct or incorrect and records nothing.

**A crew account that also has a studio** (a player made crew afterwards) can send flags, and its solves **count for its studio** like anybody's: they show on the scoreboard and in the CTFtime feed while the window is open. Use plain `test-` players for tests, and remove them afterwards (section 4).

## 2. Trying a challenge on your own computer (nothing online is touched)

The safest way to build and check challenges. The copy runs in containers on your computer and answers only to you at `http://localhost:8080`.

```bash
tools/local-test.sh start      # the first time takes a few minutes
tools/local-test.sh login      # where the crew password of this copy is kept
tools/local-test.sh stop       # stop it, keeping everything
tools/local-test.sh reset --yes   # throw everything away and start clean next time
```

It needs Docker Desktop (running), Git Bash on Windows, and Python 3.11 or newer. In this copy the broadcast is already on (the window is "from an hour ago, for 72 hours"), registration is open and nobody has to confirm an e-mail address. Sign in as `organiser` at `/admin`, make a challenge, set up the board (section 1), register a test account in a private window and play.

## 3. Rehearsing on the real platform, crew only

For what only the real machine can show: mail, Cloudflare, the clock, load, the live challenge host. **Real players who registered keep their accounts; they simply see a "not on air yet" page while it lasts.**

```bash
tools/ops/rehearsal.sh status
tools/ops/rehearsal.sh on 6      # six hours; 1 to 48
tools/ops/rehearsal.sh off
```

What `on` does, and nothing else:

- moves the broadcast window in `config/event.toml` to "ten minutes ago until six hours from now" (the real window is kept next to the file and put back by `off`);
- turns on nginx's crew-only switch: the board, the Guide, the scoreboard, the challenges, flags, hints, instances and files answer **only on the crew host name** (`crew.l3m0nctf.xyz`, behind Cloudflare Access) and to the organisers' addresses; everybody else gets "not on air yet". Registration, sign-in and the health routes still work; the public CTFtime feed reads an empty list, so the rehearsal's scores never reach CTFtime;
- restarts the platform container (about ten seconds).

**The crew's way in:** open `https://crew.l3m0nctf.xyz`, pass Cloudflare Access (the one-time code), and sign in **on that address** with a `test-` account (a session on the crew address is separate from one on `play.l3m0nctf.xyz`). The quickest way to get test accounts with no e-mail involved: in the crew pages open Users, press the plus button, give a name such as `test-anna`, an address such as `test-anna@example.com`, a password, tick *Verified*, and save. Sign in with it on the crew address, set up a studio, and play there. Several people can each use their own `test-` account, and a studio can hold up to four of them, so team play can be tried too.

While a rehearsal is on, `python -m l3mon deploy check` lists it as a thing to do, so it cannot be forgotten. **Turn it off before the real event.**

## 4. Afterwards

`tools/ops/rehearsal.sh off` puts the real window back and opens the pages. What the rehearsal made stays until it is removed: test challenges (their solves, hints and files go with them), the rehearsal's channels, `test-` accounts and their studios, "New on air" notices, the audit lines. The challenges and the plan of the real event are loaded **after** the rehearsal is cleaned, never before, so that nothing real is removed with it.

On the Release control page: *Take off the board* on every test programme, then *Remove this empty channel* on every test channel. In CTFd (`/admin`): delete the test challenges (Challenges, select, delete: their solves, hints, flags and files go with them) and the `test-` users and studios (Users, Teams). The audit lines and the "New on air" notices can stay; they are history.

## 5. Keeping the channel pages for the crew, and opening them to the play address

Before the event the channel pages (the board, the Guide, the scoreboard, the challenges, flags, hints and files) can be kept for the crew: on the crew address (`crew.l3m0nctf.xyz`) they work, and on the play address (`play.l3m0nctf.xyz`) players are told "Not on air yet". This is the same switch the rehearsal uses, on its own, with no change to the event window and no restart.

```bash
bash tools/ops/crew-gate.sh status
bash tools/ops/crew-gate.sh close     # keep the channel pages for the crew
bash tools/ops/crew-gate.sh open      # open them to everybody
```

**It must be opened before the event starts.** With the pages closed, real players cannot play. The pre-flight audit of the hour before (`tools/ops/preflight.sh --env production`) fails with `crew_only_switch` while the pages are closed, and `python -m l3mon deploy check` lists it as a thing to do.

**How to open it: ONE command, from the owner's laptop (nothing to log in to):**

```bash
bash tools/ops/gate.sh open        # in the repository folder, in Git Bash (or any bash) with gcloud signed in
```

It logs in to the server through the IAP tunnel, runs the server's own switch, and then asks the public site what a visitor gets. It ends with one of these lines:

- `GATE IS OPEN: the channels answer players.` (confirmed from outside): done.
- `STILL CLOSED: ...` or `COULD NOT CHECK from the outside ...` with a non-zero exit: do **not** assume players can get in. Run `bash tools/ops/gate.sh status`, and use the browser steps below.

`bash tools/ops/gate.sh status` shows the server's switch and what a player sees, and changes nothing. `bash tools/ops/gate.sh close --yes` closes it (it needs `--yes` because it locks the players out). It uses your gcloud configuration `l3mon-new`; no project id or password is in the script. Every toggle is written to the server's journal with who and from where: `journalctl -t l3mon-gate` on the server.

**If the laptop command cannot be used** (no gcloud, no network to Google), the same switch from the browser:

1. In the Google Cloud console open Compute Engine, then VM instances, find the machine `l3mon-app` and press **SSH** (a terminal opens in the browser).
2. Type: `cd ~/L3m0nCTF && bash tools/ops/crew-gate.sh open` and press Enter. It prints "Done. The channel pages answer everybody."
3. Check: open `https://play.l3m0nctf.xyz/board` in a private window. Before 10:00 IST on the day it shows the board's own "Not on air yet" card; from the start it shows the channels.

Opening it earlier than the start is harmless: before the start players still see "Not on air yet".

**What the gate shuts, and how a new page is added.** The list of routes is one file, `deploy/nginx/snippets/programme-routes.conf` (the board, the Guide, the scoreboard, the challenges, the story pages, a studio's public page `/teams/<number>`, the files, and the data behind them). Registration, sign-in, the player's own studio and settings, and the health routes are never shut. `tools/tests/test_gate_routes.py` reads every route the plugins register and fails until each has a recorded decision, so a new page cannot be left out by forgetting. The page a shut-out player sees (`deploy/nginx/snippets/slates/not-yet.html`) is plain, static and has links to the studio, the settings and sign-in; it is served by nginx itself, so it still works when the platform is down.

## 6. The event on now, with no date gate (`live`)

When the crew want the platform exactly as it will be on the day, without waiting for the start date: players can see what is on air, send flags, buy hints and score; the pages are open to everybody. The only difference from the day is that the clock already says "started".

```bash
bash tools/ops/rehearsal.sh live      # window open from ten minutes ago until the REAL end (28 November, 22:00 IST); pages open to everybody
bash tools/ops/rehearsal.sh status
bash tools/ops/rehearsal.sh off       # the real start (10:00 IST on 28 November) is put back; players see "Not on air yet" again
```

What `live` does, and nothing else: moves only the **start** in `config/event.toml` (the end stays where the plan has it; the real file is kept next to it and `off` puts it back), writes `default 0;` into nginx's crew-only file (the channel pages open to everybody), renders the settings and restarts the platform (about ten seconds). It refuses when a window has been moved already, when the real start has already passed, and when less than an hour is left before the real end.

**What goes on the board is still the crew's choice, programme by programme** (section 1): nothing a player can see exists until a channel and a programme are *Release now*'d on `/admin/l3mon/release`.

**Toggling a channel or a programme on and off, while live:** `/admin/l3mon/release`. *Release now* puts a channel or a programme on air; *Withhold* takes it off air at once (players stop seeing it, and a flag sent for it records nothing); *Schedule…* drops it at a chosen second (India time); *Withhold every channel* at the top of the page takes everything off air in one press. To hide **all** the channel pages from players for a while instead (for example while setting things up), `bash tools/ops/crew-gate.sh close`, and `bash tools/ops/crew-gate.sh open` to bring them back (section 5).

**Careful, it is the real platform:** the 29 registered players can see and solve whatever is on air, their solves count, and the public scoreboard and the CTFtime feed show real numbers while the window is open. Put a test programme on air only when that is wanted; clean up with section 4 before the event. `bash tools/ops/rehearsal.sh status` says whether the window has been moved. Leaving it moved through the real event does no harm (the real start has then passed; `off` after that puts the same window back); if the platform must show "Not on air yet" again before the start, run `off`.
