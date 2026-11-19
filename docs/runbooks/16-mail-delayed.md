# Runbook 16: Confirmation and reset mail is late or not arriving

## When to use this
- The alert `MailQueueGrowing` (more than 50 mails waiting), `MailQueueStale` (the oldest mail has waited more than 10 minutes) or `MailProviderCapHigh` (a provider is over 80% of its daily limit) fired.
- Players say they did not get the "confirm your account" mail, or press Resend again and again.

## What the mail relay does (so the numbers make sense)
- CTFd hands every mail to the relay (the `mail-relay` container). The relay keeps it in a queue that survives restarts and sends it through **Brevo** first, and through **Gmail** when Brevo refuses or is full.
- Each provider has its **own** daily limit over a rolling 24 hours: Brevo 290 (its free plan allows 300), Gmail 450 (a personal account allows about 500). There is also an overall ceiling (`RATE_LIMIT_PER_DAY`, 780). Up to 30 mails a minute.
- **Confirmation and reset mail goes first** and is kept for just under 5 days (CTFd's links live 5 days). Other mail is kept 72 hours. After that it is deleted unsent.
- A player who presses Resend while the first mail is still waiting does **not** add a second one: the waiting mail is replaced by the newer copy.

## 1. Look (nothing is changed)
```bash
# the queue, the oldest mail's age, each provider against its cap, and the mode
bash tools/ops/mail-upstream.sh status
# the same numbers as the dashboard "L3m0nCTF 2026 - Mail and Backups" in Grafana
```
Read: `l3mon_mail_queue_length`, `l3mon_mail_queue_age_seconds`, `l3mon_mail_sent_24h{provider=...}` against `l3mon_mail_cap{provider=...}`, `l3mon_mail_failed_total`, `l3mon_mail_holding`.

## 2. Decide
| What you see | What it means | What to do |
|---|---|---|
| `l3mon_mail_holding 1` or mode `hold` | someone paused the relay, or no provider has a password | `bash tools/ops/mail-upstream.sh auto`; check the two password files (step 3) |
| Both providers at their cap | more mail than the two accounts may send today | wait (the oldest delivery leaves the 24-hour window every few minutes), or raise a cap **only** if that provider's real limit allows it (step 4); the confirmation link stays valid for 5 days, so late mail still works |
| One provider at its cap, the queue still growing | the other provider is failing | `l3mon_mail_failed_total` and the relay log (step 3) show the error; fix that provider, or pin the mode to the working one (`brevo` or `gmail`) and set it back to `auto` afterwards |
| Queue empty, players still say nothing came | the mail was sent but not delivered | check the spam folder, the Brevo dashboard (blocked sender, bounces), and that SPF, DKIM and DMARC for `l3m0nctf.xyz` are set (owner, Cloudflare DNS) |

## 3. The relay's log and the passwords
```bash
docker logs --tail 100 $(docker ps -qf name=mail-relay)
```
Look for `Delivery failed` lines and their reason (a refused login means the password is wrong or was rotated). The Brevo key is the file `.secrets/MAIL_PASSWORD`; the Gmail app password is the file `.secrets/FALLBACK_MAIL_PASSWORD` (not a line in `production.env` any more). Never paste either into a chat or a terminal that is logged. After a change to a password file: `tools/compose.sh up -d mail-relay` (the relay restarts in seconds; the queue is kept).

## 4. Raising a limit (only when the provider's own limit allows it)
Set `PRIMARY_DAILY_CAP`, `FALLBACK_DAILY_CAP` or `RATE_LIMIT_PER_DAY` in `/etc/l3mon/production.env` (use `sudo sed -i`), then `tools/compose.sh up -d mail-relay`. Gmail may suspend sending for a day if its real limit is passed, so do not set the Gmail cap above what the account allows.

## 5. What not to do
- Do not delete the spool volume or restart ctfd to "fix" mail: the queue lives in the relay's volume, and mail that is waiting is real.
- Do not leave the mode on `hold`, `gmail` or `brevo` after the incident: `auto` is the setting that keeps both providers working.
- Do not tell players to press Resend repeatedly: it is harmless now (copies are merged), but it does not make the mail come faster.

## 6. After
Write what happened (time, which provider, which number) in `docs/deploy/verification-log.md`.
