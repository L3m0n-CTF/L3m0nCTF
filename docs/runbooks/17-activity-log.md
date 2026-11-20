# Runbook 17: The activity log, studio removals and renames

## What this is
Since 20 Nov 2026 the platform keeps an **activity ledger**: one line for each thing a participant did (who, what, which studio, when, from which address and browser). It exists so that when somebody asks "who removed her?", "who renamed the studio, and when?" or "did this account really sign in from there?", the answer is a line, not a memory.

## What is recorded
| Kind | When |
|---|---|
| `studio.create` | a studio is made |
| `studio.join`, `studio.leave`, `studio.switch` | a person's studio changes, however it happened (the page, the API, the crew) |
| `studio.remove` | a captain removed a member (the name, and whether the member had a solve or a hint unlock in the last day) |
| `studio.removal.lift` | the crew let a removed person back |
| `studio.join.refused` | a removed person tried to join the same studio again |
| `studio.rename` | the name changed: old and new, who, when (the page, the API and the crew all leave this line) |
| `studio.rename.refused` | a captain asked for a name the rule does not allow (once a day; locked from the start; locked after the first solve) |
| `studio.captain` | the captain's chair changed hands |
| `auth.login.ok`, `auth.logout` | somebody signed in or out: seen as the session changing, so Google and CTFtime sign-ins count like a password one |
| `auth.login.fail` | a refused sign-in. **The text typed is never kept** (people type an address, even a password, into the name box by mistake): the line names the account by number when it matches one, otherwise only a keyed hash (`typed`, 12 characters: the same text gives the same mark, so a guessing run shows, but it cannot be read back), its shape (`name` or `email`) and an address's domain |
| `auth.register`, `auth.register.fail` | a new account (only the address's DOMAIN is written) and a refused registration (the name tried) |
| `auth.confirm.resend`, `auth.confirm.ok`, `auth.confirm.fail`, `auth.reset.request`, `auth.reset.done`, `auth.reset.fail` | the confirmation mail and the password reset |
| `account.update` | which FIELDS changed on the settings page or through the API (never their values) |
| `hint.unlock` | a hint was bought |
| `instance.start`, `.renew`, `.stop`, `.start.refused` ... | a challenge instance (with the status when refused) |
| `crew.<action>` | every audit line of an administrator or of the platform itself (one timeline) |

Flag attempts are **not** copied here: CTFd's own `submissions` table already keeps them (the text, the time and the address), so a flag is never in two places.

**Never recorded:** passwords, tokens, CSRF tokens, cookies, whole e-mail addresses (only their domain), and flag text, whatever a caller passes in (`activity.py` removes them before anything is written or printed).

## The rules the captain lives under (policy decided 19 Nov 2026)
- **Removing a member:** the captain only; not themself, not the captain; **at most 2 removals per studio per day** (`l3mon_removals_per_day`). What the member earned (solves, hint unlocks, awards) **stays with the studio**; TRP does not move. The removed person cannot join the **same** studio again (by invite link or by name and password) until the crew lifts it; they can join or create another one. If the member had a solve or a hint unlock in the 24 hours before, the removal is flagged for the crew (`recent_activity` on the removal, and a line `studio.remove.recent` in the crew audit): the "lend my flag, then leave" pattern.
- **Renaming:** the captain, **once a day**, **locked for good from 28 Nov 2026 10:00 IST** (`l3mon_rename_locked_from`, UTC epoch seconds) **or after the studio's first solve**, whichever comes first. The crew can always rename. The same rule holds on the page and on the API (`PATCH /api/v1/teams/me`).
- Both settings are changed like the other crew settings: `PATCH /api/v1/configs` with an administrator token (`{"l3mon_removals_per_day": "3"}`).

## Reading the ledger
- **On the crew page** `/admin/l3mon/activity` (menu: "Activity log", crew address only): filter by studio number, person number and kind; **Check the chain** (and whether the database refuses changes); **Download these lines (CSV)**; and below, the removals still in force with a **Let them back** button (it asks first). Cells that start with `=`, `+`, `-` or `@` get a leading apostrophe in the CSV so a spreadsheet does not run them.
- **In the database** (it is in every backup): table `l3mon_activity`. Newest first for a studio: `SELECT at, kind, actor_name, target, detail FROM l3mon_activity WHERE team_id = 123 ORDER BY id DESC;` (use the crew's read-only database access, never an `UPDATE`).
- **In the log** (a second copy the database cannot erase): every line is also printed as one JSON line on the platform container's output, which the log shipper takes to Loki. In Grafana (Explore, Loki): `{container="l3mon-ctfd-1"} |= "l3mon_activity"`. **Loki keeps the log copy for 30 days** (720 hours; the database copy stays for good and is in every backup). A line with `"lost": true` means the database write failed; the line is still there.

## Checking that nothing was changed or removed
```bash
cd ~/L3m0nCTF && docker exec "$(tools/compose.sh ps -q ctfd)" l3mon-run /opt/venv/bin/python -c "
from CTFd import create_app
from CTFd.plugins.l3mon_core import activity
app = create_app()
with app.app_context():
    print(activity.verify_chain())
    print('database refuses changes (triggers):', activity.triggers_installed())"
```
`ok=True` means every row still matches the hash of the row before it. `ok=False` names the first row (`first_bad_id`) that was changed, or that no longer follows its predecessor (a row is missing). Then compare with the log copy in Loki around that time, and tell the owner **before** anything is repaired: a broken chain is evidence.

## At every deploy: the triggers
The database refuses `UPDATE` and `DELETE` on the ledger by **triggers**. Production's MariaDB has binary logging on, and then `CREATE TRIGGER` is refused to the platform's own database user (ERROR 1419), so the platform's migration only *tries* (and logs a warning that names this step); the triggers are made as the database's root user by:
```bash
bash tools/ops/activity-triggers.sh apply     # after the platform has started once; safe to repeat; says "IN PLACE" when both exist
bash tools/ops/activity-triggers.sh status    # exit 0 only when both are in place
```
The deploy script runs it after the platform is up. It uses `CREATE TRIGGER IF NOT EXISTS` and never drops a trigger first, so the table is never unprotected.

## What this protects against, honestly
- **Edits and deletions by a mistake, a bad query or a bug:** refused by the triggers.
- **Edits by somebody with the database's rights (or a compromised platform, which has all rights on its own database):** they can drop a trigger, but the hash chain finds a changed or a missing row, and the copy in Loki still holds the line. It is **tamper-evident, not tamper-proof**.
- **Loss of the log copy:** the database copy remains (and is in the backups).
- **A line that could not be written:** printed with `"lost": true`; the platform never fails an action because the ledger failed.

## Lifting a removal (the crew)
The removed person is refused with a plain sentence ("You were removed from this studio by its captain. If that was a mistake, ask the crew."). To let them back: crew page, "Members removed from a studio", **Let them back** (or `POST /api/v1/l3mon/admin/removals/<number>/lift`). The lifting is recorded (`studio.removal.lift`, with who) and in the crew audit.
