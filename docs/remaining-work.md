# What is left to build, decide and verify

Written **21 Nov 2026** after a review of the whole codebase, the first real-host test round of the instancer, and read-only checks of production. It replaces the October version of this page, which listed parts that have since been built. The online round is **28 November 2026, 10:00 IST**; the rehearsal is **27 November**.

## Where the code stands

The code itself is in good shape: no stubs and no TODO markers anywhere in the plugins, the services or the tools; every skipped test is skipped for a reason of its environment (no MariaDB, no Docker, Windows); CI runs ten jobs (hygiene, lint, image build and 37 image checks, the plugin suite of about 3,300 tests, CTFd's own 676, integration, overlays, the instancer, third-party image scan, the firewall experiment) and was green on the last release. Built and deployed: the theme and all player pages, scoring and the freeze rules, the release control, anti-abuse, CTFtime and Google sign-in, the story pictures, the mail relay with two providers, captains removing members and the rename policy, the append-only activity log, the crew-only switch, and backups every 30 minutes. The instancer is now proven on a real challenge host (106 checks, plus the owner's own challenges and solvers).

What is left is mostly **operations, capacity, and getting the challenges in**, not features.

## A. Only you or your superiors can decide or do these

| # | What | Why it matters | Needed by |
|---|---|---|---|
| A1 | **Approve the size of the machines for the event**: platform 16 vCPU / 32 GB, two challenge hosts of 8 vCPU / 16 GB (an estimate from Google's list prices, not a bill: about 0.27 US dollars an hour for each challenge host and about 0.55 for the platform, so about 1.1 an hour together and roughly 20 to 25 dollars for the rehearsal and the event day), and ask Google for a **64 vCPU quota** (it is 32; the plan uses exactly 32) | the platform is on 2 vCPU now; 878 players need about 8 cores of CTFd by the October measurement | the quota request now (it takes hours to days); resize before 27 Nov |
| A2 | **Cloudflare Access for the instance addresses**: add `*.l3m0nctf.xyz` to the crew application, Bypass applications for `play`, `www` and the apex, then the wildcard DNS record last (steps in `private/INSTANCER-TEST-DAY-owner-sheet.md`); give me the crew application's Audience tag | the crew-only gate for the test round, and the first full test of the platform with the instancer | before the platform half of the instancer test |
| A3 | **Alerts have nowhere to go**: Alertmanager's only receiver is `blackhole` and the Discord webhook secret is empty. Choose Discord (create a webhook) or e-mail, and who is paged | nobody would be told if the platform went down during the event | before 27 Nov |
| A4 | **Off-machine backups**: backups run every 30 minutes but only on the VM's own disk. Choose a Cloud Storage bucket in the project (I recommend it) or your laptop | losing the VM would lose the backups with it | before 27 Nov |
| A5 | **Named crew accounts**: there is one admin account. Your superiors asked for 2 to 3; the activity log attributes crew actions to accounts, so one shared account hides who did what. Tell me the names and e-mails | accountability, and runbook step 6.3 | before 27 Nov |
| A6 | **Turnstile (CAPTCHA) keys** from Cloudflare (site key and secret) | the anti-abuse layer that stops bots registering is built but has no keys | before the event |
| A7 | **Privacy wording** approved by the superiors, with their three answers (retention, who is crew, subject access): `private/PRIVACY-activity-log-wording-2026-11-20.md` | the activity log already records real participants | now |
| A8 | **Rotate the Gmail app password** (it was in a file and in a chat) | the mail fallback | now |
| A9 | **Challenges that cannot run per team as written** (decisions, see B3 and B4): UPI Sandbox Bypass, and the three Ollama (LLM) challenges | they need a feature the platform deliberately does not have, or a design change by the author | before the challenge set is frozen |
| A10 | **Per-team downloads**: no part of the platform builds a different file per team. Accept shared files with one static flag for the first event (my recommendation), or ask for it to be built | static challenges with a per-team flag | before the challenge set is frozen |
| A11 | **A second domain for the instance addresses** (about 2 dollars a year): the instances share a site with the platform, so a challenge page can fill a visitor's browser with cookies until the platform refuses their requests (no code change needed) | the one open risk of the shared domain | before the event |
| A12 | **The authors' images**: who builds and pushes the 30 authors' challenge images to the registry by digest, and where (GitHub Container Registry as planned) | the instancer only runs images pinned by digest | now |
| A13 | The remaining sponsor website (Knight Secured); the offline finals at Amrita (on hold) | | |

## B. Only I can build these (with rough sizes)

| # | What | Size | Notes |
|---|---|---|---|
| B1 | **Challenge sync**: turn an author's bundle into a challenge on the platform: the CTFd challenge, its place in the programme, its files, the flag type, and for a live challenge the instancer stanza with the image digest. Nothing does this today; the author kit exports files only, and its flag type names do not match the platform's | 1-2 days | the biggest remaining build; every one of the 150-200 challenges needs it |
| B2 | **Run the platform half of the instancer test on production** (start an instance from the platform page, through the central router, the crew gate and Cloudflare): needs a deploy (a release tag, with your yes) and A2 | half a day | the one path no test has walked end to end |
| B3 | **Multi-container challenges and controlled outbound access** (UPI Sandbox Bypass needs two containers, `NET_ADMIN` and DNS to the player's own server): a design with real security cost; I recommend the author reworks the challenge instead | 2-3 days if wanted | decision A9 |
| B4 | **A shared service next to the instances** (the Ollama challenges): a model server that every instance can call. CPU inference of a 1B model for hundreds of players at once is not realistic; I recommend a hosted LLM API with a rate limit, or fewer simultaneous players on those challenges | decision first | decision A9 |
| B5 | **Monitoring of the challenge hosts**: Prometheus does not scrape them at all today (no CPU, memory, disk, agent or router metrics, no alerts for a full host, a down host or gate refusals) | half a day | after A3 |
| B6 | **The platform page waits for the instance to be live**: for up to a second after Start the address answers 404 (the routers poll every 0.5 s); the page should show "starting" until it answers | 2 hours | plugin and page |
| B7 | **Stop all instances when the contest ends** (today only a time-up of at most 45 minutes, or the crew's kill call, ends them) | 2 hours | |
| B8 | **A crew page for instances** (list, kill by team or challenge; today only the API) | half a day | nice to have |
| B9 | **A load script for the rehearsal** (the existing probe refuses anything but a throwaway local stack): sign in several hundred players, poll, submit flags, start instances; run it on 27 Nov and read the numbers | 1 day | the platform's capacity on cloud cores has never been measured |
| B10 | **Tune the platform for the bigger machine**: `CTFD_WORKERS`, MariaDB buffer pool (384 MB now), Redis memory, nginx limits; then resize and re-run the checks | 2 hours | after A1 |
| B11 | **Rebuild the penetration-test bundle** from the current images and give it to the testers; fix what they find | 1 day | leadership's test |
| B12 | **Replace the nginx image** (deadline 25 Nov, seven accepted advisories), re-run the scan, the integration tests and the restore drill | 2 hours | |
| B13 | Author kit: tell authors the instancer rules (the checklist in `docs/deploy/instancer.md` section 4d) and make the checker flag a challenge that writes outside `/tmp` | half a day | |
| B14 | Real-concurrency proof of two simultaneous renames on the full stack (`tests/integration/test_races.py`) | 2 hours | |

## C. To verify (nothing to build, but not yet proven)

1. **The platform under load on cloud cores** (B9, on 27 Nov). Only the October laptop figure exists. Until then "878 players online at once" is an estimate.
2. **The whole event day, once, by someone who did not write the runbooks** (`runbooks/01-event-start.md` still has "followed once by someone who did not write it" unchecked): the 27 Nov rehearsal.
3. **A restore on production** (the drill passes in the test environment: it destroys a whole stack and restores it; it has not been done on the real VM).
4. **Cloudflare**: strict mode, client-certificate check, the rate-limit rule and Access on `crew` are in the go-live checklist but I cannot see them; please confirm they are set.
5. **Mail delivery to the participants' own domains** (a test to a few college addresses, spam folder included), with the two-provider caps (290 and 450 a day, 780 ceiling) against 878 participants plus resends.
6. **A real pwn exploit loop on 50 teams at once** against the challenge hosts, to see CPU rather than memory.
7. **The challenges themselves**: each one's author solver against a live instance for two teams (the method used on 21 Nov for five challenges); the static ones need no hosting.

## D. What the 21 Nov test round settled

- The instancer's design holds: isolation (no way out, no way across, no way up), limits (memory, processes, CPU, disk), per-team flags, recovery from crashes, restarts and a host reboot, and a burst of 80 simultaneous starts, all proven on a real host. It found and fixed eleven defects (listed in `docs/deploy/verification-log.md`, 21 Nov, and in `docs/deploy/instancer.md`, sections 4a to 4d), the worst being that an instance could reach other teams' TCP instances through the router.
- The crew-only gate is built and tested; it needs A2 to be switched on.
- Of the owner's five tried challenges, two run as delivered, three need a few lines in the Dockerfile or entry-point (listed), and the three LLM challenges and UPI cannot run per team.
- Capacity: see `docs/deploy/instancer.md`, "Sizing for 878 players in 446 teams". The challenge side is sized from measurements; the platform side from an October estimate.
