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

**Try as a player.** Crew accounts have no studio, so they cannot send a flag. To play, register an ordinary account, set up a studio, and sign in with that. Name test accounts `test-` and something (`test-anna`), so that they can be found and removed.

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

**How to open it (the person who deploys, from any computer):**

1. In the Google Cloud console open Compute Engine, then VM instances, find the machine `l3mon-app` and press **SSH** (a terminal opens in the browser).
2. Type: `cd ~/L3m0nCTF && bash tools/ops/crew-gate.sh open` and press Enter. It prints "Done. The channel pages answer everybody."
3. Check: open `https://play.l3m0nctf.xyz/board` in a private window. Before 10:00 IST on the day it shows the board's own "Not on air yet" card; from the start it shows the channels.

To close it again, the same with `close`. Opening it earlier than the start is harmless: before the start players still see "Not on air yet".
