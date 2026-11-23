# Runbook 20: the channel bonus

Written 23 Nov 2026 (the owner's request: every studio is given a fixed number of TRP for completing each channel).

## What a studio gets

When a studio has solved **every programme of a channel**, the platform gives it a **fixed number of TRP, once for that channel**. The default is **100 TRP**, set by the crew (0 switches it off, 1000 is the most). It is the same number for every channel, a sponsored channel included (its TRP counts in the main ranking). It counts everywhere TRP counts: the studio's score, the ranking, the scoreboard and the CTFtime feed. The studio sees it as "Channel bonus +100 TRP" in the Guide's bonus total and in its private lines (the bell), and the reply to the completing flag says `channel_bonus: 100`.

| Question | Answer |
|---|---|
| Which solves complete a channel? | every programme the plan puts in the channel, **on air or not**: a programme still withheld has to be solved when it comes on air. A channel with no programme never completes |
| Who gets the award? | it is a plain bonus award on the studio's **captain** (the first member if the studio has no captain), like the crew's own bonus, so it counts for the whole studio |
| Can it be given twice? | no: the database has one record per studio and channel, even if two members finish the channel at the same moment |
| When is it given? | at the end of the request that records the last solve (a player's flag, or "mark correct" by the crew, or a Restore), and at the latest within a minute of any page load (the minute check), which also gives it to solves written straight into the database |
| What if the crew adds a programme to the channel later? | the studio **keeps** the bonus: it earned it |
| What if a solve of that channel is set aside (Revoke)? | the bonus is **taken back** when the channel is no longer complete, the studio is told in its private lines, and a Restore **gives it again** (the same record, a new award) |
| What if the amount is changed? | what was already given stays as it was; the new amount applies from now on. Raising it from 0 pays the channels already completed within a minute |
| Deleting a solve in CTFd's own admin (mark incorrect) | the bonus is taken back the same way |
| Deleting a **user** (CTFd removes their solves in bulk) | the bonus is **not** taken back: use Revoke for a broken challenge, and a removal of a member leaves the studio's bonus where it is |

## See and change the amount

On the crew's Scoring page (`/admin/l3mon/scoring`, crew host) the section **Channel bonus** shows the amount and a box to change it ("0 switches it off"); it asks you to confirm. Every change is in the audit trail (`scoring.channel_bonus`) and in the activity log.

Without the page, from the browser console of any crew admin page:

```js
fetch('/api/v1/l3mon/admin/scoring/channel-bonus', {method: 'PUT', credentials: 'same-origin',
  headers: {'Content-Type': 'application/json', 'CSRF-Token': window.init.csrfNonce}, body: JSON.stringify({trp: 150})}).then(r => r.json()).then(console.log)
```

`{"success": true, "data": {"trp": 150, "was": 100}}` means it is set. The amount is stored in CTFd's own settings (`l3mon_channel_bonus_trp`), so it is in every backup.

## Check that it works (about five minutes, before the event)

1. Use a test studio and a channel of two programmes (the demo round of runbook 19 has them): solve the first, the score rises by that programme's value only; solve the second, it rises by the value **plus the channel bonus** and the reply says `channel_bonus`.
2. The Guide shows the bonus in the studio's total; the bell shows "Channel bonus +100 TRP".
3. Revoke the second programme: the score drops by its value **and** the bonus, the studio's private line says the bonus was withdrawn; Restore: both come back.

## The numbers behind it

One record per studio and channel in `l3mon_channel_bonus` (state `awarded` or `revoked`, the amount, the award it created). The channel bonus never changes a programme's own value or the dynamic scoring. Nothing here runs unless the amount is above 0.
