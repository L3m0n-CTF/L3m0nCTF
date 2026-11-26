# Runbook 21: the Beginners Section (channel 0, "Test Card")

Written 26 Nov 2026. The Beginners Section is 15 guided challenges for people who have never played a CTF, made by the owner's superior. It reaches players in two parts that work together:

| Part | Where it lives | What it does |
|---|---|---|
| **The lessons** | a small website, `https://beginner.l3m0nctf.xyz` (one extra container behind our nginx, `deploy/beginner/`) | the teaching page of each challenge, its practice files, the live labs (3 web, 2 stack simulators, the candy shop), a flag checker for learning |
| **The challenges** | the platform, channel 0 (`test-card`), 15 programmes | the task text with a link to the lesson, the points, the flag, the scoreboard: **the only place a flag counts** |

A player opens a programme on the platform, follows the link in its task text, learns and solves on the beginner site, and brings the flag back to the platform. (The player page shows an `https` link in a task text as a clickable link that opens in a new tab; the page without script shows it as plain text to copy.)

## What is NOT in this repository, on purpose

The source of the site (`app.py`, `challenges.py`, `templates/`, `static/`) holds **every flag** (the lessons need them), and this repository is public. It lives in the owner's private repository and in the author's zip. `tools/ops/beginner.sh pack` makes the archive the image is built from (the author's four things plus our `serve.py` and `guard.py`, which put a time budget (half a second), a size limit, a cap of 64 MB on all the memory SQLite may use, and tight limits on one statement (short LIKE pattern, few compound terms, no ATTACH) under the SQL labs so that one heavy query cannot take the site down: the lesson still works), and the repository's scanner (`hygiene`) would fail the build if a flag ever got in here.

## Decisions that are the owner's (and his superiors'), not the code's

1. **When the site goes up.** The labs and the downloads hand out the flags to anybody who reaches them, and the flags are the same for everybody. Up before the contest starts means anyone can collect all 15 in advance. Choices: bring it up at the start, or accept it as practice. Taking the container down again: remove `beginner` from `L3MON_OVERLAYS` and run `tools/compose.sh up -d --wait`.
2. **Points.** The import uses the author's points as fixed values (50 to 125, 1,200 in all). The platform's other challenges may use a falling value; say if these should too.
3. **The channel bonus.** Channel 0 is an ordinary channel, so a studio that solves all 15 is paid the channel bonus (default 100 TRP, not yet confirmed by the owner). Say if it should not apply.
4. **Names.** Titles and practice texts use cartoon names (Cookie Monster, Cartoon Network, Toontown). The author kit's lists do not block them; its guide says original names only. The author's call.

## Put it on the platform (needs the owner's yes: it recreates nginx, a few seconds)

On this computer, with the Beginners Section folder (the one with `app.py`):

```bash
bash tools/ops/beginner.sh pack "<folder with app.py>" D:/work/l3mon-beginner/src.tgz
```

Copy it to the platform VM, build the image there (never on the challenge host), and put the settings in place:

```bash
gcloud --configuration=l3mon-new compute scp --zone=asia-south1-a --tunnel-through-iap D:/work/l3mon-beginner/src.tgz l3mon-app:src.tgz
# on the VM:
mkdir -p ~/beginner-src && tar -xzf ~/src.tgz -C ~/beginner-src && rm -f ~/src.tgz
cd ~/L3m0nCTF && docker build -q -f deploy/beginner/Dockerfile -t l3mon/beginner:<tag> ~/beginner-src && rm -rf ~/beginner-src
sudo sh -c 'printf "BEGINNER_TAG=<tag>\nBEGINNER_SECRET_KEY=%s\n" "$(openssl rand -hex 32)" >> /etc/l3mon/production.env'
# add ,beginner to L3MON_OVERLAYS in /etc/l3mon/production.env, then:
set -a; . /etc/l3mon/production.env; set +a; tools/compose.sh up -d --wait
```

The nginx block answers only the exact name `beginner.l3m0nctf.xyz` (a different domain means editing the `server_name` line in `deploy/nginx/beginner/beginner.conf`). Check from outside: `https://beginner.l3m0nctf.xyz/` answers and a lesson opens (`/c/behind-the-curtain`); the platform (`/readyz`, `/login`) still answers. The block's upstream is looked up per request: if the container is not there, only `beginner.` answers 502.

The challenges, with a dry run first (nothing is written without `--apply`; the flags file is the author's `flags/beginner_flags.json` and stays on this computer except for the length of the call):

```bash
bash tools/ops/beginner.sh import "<folder>/flags/beginner_flags.json"
bash tools/ops/beginner.sh import --apply "<folder>/flags/beginner_flags.json"
```

Everything lands **off air**: hidden challenges, channel `test-card` and 15 programmes withheld, nothing announced. Look at them on the crew's page (`/admin/l3mon/release`), then release channel 0, and each programme, when the owner decides. Running the import again changes nothing; a challenge or flag that already exists is never touched.

## Take it back

Remove `beginner` from `L3MON_OVERLAYS` and `tools/compose.sh up -d --wait`: nginx is recreated without the block and the container is removed. The 15 challenges stay hidden and off air; to remove them, take the programmes off the board on the crew's page (the challenges stay in CTFd, on no channel, hidden).

## What was proved before this was written (26 Nov 2026)

The image was built from the real source and run with the overlay's settings (read-only, no capabilities, unprivileged user, 256 MB): all 15 lessons, the 7 lab pages, the downloads and the flag checker work, each of the 15 flags in the author's export is accepted by the site for its own challenge and for no other, nothing outside the allowed downloads can be fetched (path tricks, `app.py`, `challenges.py`, the flags file), and the three web labs, the stack simulator and the candy shop give their flag only to the intended move. It used 32 MB of memory idle. The import is proved in the platform's plugin suite (`plugins/l3mon_release/tests/test_beginner.py`), the image, overlay and nginx block in `tools/tests/test_beginner_overlay.py` (including real `docker compose config` and real `nginx -t`).
