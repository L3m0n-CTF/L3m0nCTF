"""Discord posts for the first, second and third blood of a programme (the BloodSeeker channel).

Where the address comes from: the Docker secret DISCORD_BLOOD_WEBHOOK (the file .secrets/DISCORD_BLOOD_WEBHOOK, mounted at
/run/secrets/DISCORD_BLOOD_WEBHOOK; L3MON_DISCORD_BLOOD_WEBHOOK_FILE names another file). A missing or empty file means no posts. The
address is NEVER in this repository: anyone who has it can post as the bot (docs/runbooks/09-rotate-secrets.md says how to change it).

What is posted, and when nothing is:
- only studios that count: a hidden or banned studio (the crew's, a test studio) never posts and never takes a blood from another;
- the count is the real one, never the freeze-filtered count a studio is told in its reply;
- nothing for a solve at or after the scoreboard freeze: a post would tell everyone what the frozen board hides (no post is made
  later either);
- nothing from a test app (CTFd's TESTING), so a test run never reaches the real channel, even on a machine that has the file;
- the studio's place and TRP are the public scoreboard's (hidden studios are not counted);
- studio and programme names are escaped for Discord's markdown and no mention is ever parsed (an `@everyone` in a name stays text).
A Revoke after a post does not take the post back: the crew deletes it in Discord by hand.
"""
import json
import logging
import os
import re
import threading
import urllib.request

log = logging.getLogger("l3mon.blood")

SECRET_FILE = "/run/secrets/DISCORD_BLOOD_WEBHOOK"
_LOCK = threading.Lock()
_NOTIFIED_SOLVES = set()

# Embed left accent border colors (decimal RGB)
COLOR_FIRST_BLOOD = 16765503   # Gold / Yellow (#ffd23f)
COLOR_SECOND_BLOOD = 14866672  # Silver / Light Grey (#e2e8f0)
COLOR_THIRD_BLOOD = 13467442   # Bronze / Amber (#cd7f32)

_WORDS = ("first", "second", "third")
_TITLES = ("First Blood!", "Second Blood!", "Third Blood!")
_COLORS = (COLOR_FIRST_BLOOD, COLOR_SECOND_BLOOD, COLOR_THIRD_BLOOD)
_ALLOWED = re.compile(r"^https://(?:discord\.com|discordapp\.com|ptb\.discord\.com|canary\.discord\.com)/api/webhooks/\d+/[A-Za-z0-9_-]+$")


def get_webhook_url() -> str:
    """The webhook address from the secret file, or "" (no posts) when there is none, it is empty, or it is not a Discord webhook."""
    path = os.environ.get("L3MON_DISCORD_BLOOD_WEBHOOK_FILE") or SECRET_FILE
    try:
        with open(path, encoding="utf-8") as f:
            url = f.read().strip()
    except OSError:
        return ""
    if not url:
        return ""
    if not _ALLOWED.match(url):
        log.warning("The blood webhook file does not hold a Discord webhook address: no blood posts")
        return ""
    return url


def ordinal(n) -> str:
    if n is None:
        return "1st"
    s = str(n).strip()
    # If it's already an ordinal like '1st', '2nd', '3rd', '35th'
    if re.search(r"^\d+(?:st|nd|rd|th)$", s, re.IGNORECASE):
        return s.lower()
    m = re.search(r"\d+", s)
    if not m:
        return "1st"
    val = int(m.group())
    if 11 <= (val % 100) <= 13:
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(val % 10, "th")
    return f"{val}{suffix}"


_MARKDOWN = re.compile(r"([\\*_~`|>\[\]()#:<@-])")


def escape(text) -> str:
    """A name as plain text in a Discord embed: one line, every markdown character escaped (no bold, masked link, heading or link)."""
    line = " ".join(str(text or "").split())
    return _MARKDOWN.sub(r"\\\1", line)


def get_team_rank_and_score(team):
    """The studio's place and TRP on the public scoreboard (hidden and banned studios are not counted)."""
    score = 0
    rank = None
    try:
        score = int(team.get_score(admin=False) or 0)
    except Exception:
        pass
    try:
        rank = team.get_place(numeric=True, admin=False)
    except TypeError:
        try:
            rank = team.get_place(admin=False)
        except Exception:
            pass
    except Exception:
        pass
    return rank, score


def create_blood_embed(blood_index: int, team_name: str, challenge_name: str, rank, score: int) -> dict:
    """The Discord embed (BloodSeeker style). blood_index: 0 first, 1 second, 2 third blood; anything else gives {}."""
    if blood_index not in (0, 1, 2):
        return {}
    return {
        "title": _TITLES[blood_index],
        "description": f"{escape(team_name)} got {_WORDS[blood_index]} blood on {escape(challenge_name)} and is now in {ordinal(rank)} place with {int(score or 0)} TRP!",
        "color": _COLORS[blood_index],
    }


def _send_webhook(url: str, payload_data: dict):
    try:
        payload = json.dumps(payload_data).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=payload,
            headers={
                "Content-Type": "application/json",
                "User-Agent": "L3m0nCTF-BloodBot/1.0",
            },
        )
        with urllib.request.urlopen(req, timeout=8):
            pass
    except Exception as exc:
        # never the address in the log: the exception text of a failed request can carry it
        log.warning("Failed to post a blood notification to Discord: %s", type(exc).__name__)


def _in_test_app() -> bool:
    try:
        from flask import current_app
        return bool(current_app.config.get("TESTING"))
    except Exception:
        return False


def _frozen_for(solve) -> bool:
    """True when the solve is at or after the scoreboard freeze (the public board does not show it)."""
    from CTFd.plugins.l3mon_core.clock import window
    from CTFd.utils.dates import unix_time_to_utc
    freeze = window().freeze
    date = getattr(solve, "date", None)
    return bool(freeze) and (date is None or date >= unix_time_to_utc(freeze))


def _earlier_count(challenge, solve) -> int:
    """Solves of this programme recorded before this one, by studios that count (no freeze filter)."""
    from CTFd.models import Solves, Teams
    return Solves.query.join(Teams, Solves.account_id == Teams.id).filter(
        Solves.challenge_id == challenge.id,
        Solves.id < solve.id,
        Teams.hidden == False,  # noqa: E712 (SQL, not Python)
        Teams.banned == False,  # noqa: E712
    ).count()


def notify(challenge, team, solve):
    """Post the first, second or third blood of this solve to Discord, in the background. Never raises into the flag's reply."""
    if challenge is None or team is None or solve is None:
        return
    if getattr(team, "hidden", False) or getattr(team, "banned", False):
        return
    if _in_test_app():
        return
    webhook_url = get_webhook_url()
    if not webhook_url:
        return
    if _frozen_for(solve):
        return

    solve_id = getattr(solve, "id", None)
    if solve_id is not None:
        with _LOCK:
            if solve_id in _NOTIFIED_SOLVES:
                return
            _NOTIFIED_SOLVES.add(solve_id)

    embed = create_blood_embed(_earlier_count(challenge, solve), team.name, challenge.name, *get_team_rank_and_score(team))
    if not embed:
        return
    payload_data = {"embeds": [embed], "allowed_mentions": {"parse": []}}
    threading.Thread(target=_send_webhook, args=(webhook_url, payload_data), daemon=True).start()
