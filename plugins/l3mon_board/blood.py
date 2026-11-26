"""Discord notifications for first, second, and third blood on challenge solves.

Webhook:
  Configured by L3MON_DISCORD_BLOOD_WEBHOOK or DISCORD_BLOOD_WEBHOOK environment variable.
  Defaults to the official CTF Discord BloodSeeker webhook.
"""
import json
import logging
import os
import re
import threading
import urllib.request

log = logging.getLogger("l3mon.blood")

DEFAULT_WEBHOOK = "https://discord.com/api/webhooks/1449642076215181464/dRFI-C8I-nEvSSIbdTgdbzcwZ7fWe_wze784J5n3z4ICDskCEaddRUaFB_PzqZ6TPBhn"
_LOCK = threading.Lock()
_NOTIFIED_SOLVES = set()

# Embed left accent border colors (decimal RGB)
COLOR_FIRST_BLOOD = 16765503   # Gold / Yellow (#ffd23f)
COLOR_SECOND_BLOOD = 14866672  # Silver / Light Grey (#e2e8f0)
COLOR_THIRD_BLOOD = 13467442   # Bronze / Amber (#cd7f32)


def get_webhook_url() -> str:
    url = os.environ.get("L3MON_DISCORD_BLOOD_WEBHOOK") or os.environ.get("DISCORD_BLOOD_WEBHOOK") or DEFAULT_WEBHOOK
    url = url.strip()
    if url.lower() in ("", "none", "false", "0", "off"):
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


def get_team_rank_and_score(team):
    score = 0
    rank = None
    try:
        score = int(team.get_score(admin=True) or 0)
    except Exception:
        pass
    try:
        rank = team.get_place(numeric=True, admin=True)
    except TypeError:
        try:
            rank = team.get_place(admin=True)
        except Exception:
            pass
    except Exception:
        pass
    if rank is None:
        try:
            from CTFd.utils.scores import get_standings
            standings = get_standings(admin=True)
            for idx, row in enumerate(standings, 1):
                if int(row.account_id) == team.id:
                    rank = idx
                    break
        except Exception:
            pass
    return rank, score


def create_blood_embed(blood_index: int, team_name: str, challenge_name: str, rank, score: int) -> dict:
    """Create a Discord Embed payload matching the BloodSeeker style:
    blood_index: 0 for First Blood, 1 for Second Blood, 2 for Third Blood.
    """
    rank_str = ordinal(rank)

    if blood_index == 0:
        return {
            "title": "First Blood!",
            "description": f"{team_name} got first blood on {challenge_name} and is now in {rank_str} place with {score} points!",
            "color": COLOR_FIRST_BLOOD,
        }
    elif blood_index == 1:
        return {
            "title": "Second Blood!",
            "description": f"{team_name} got second blood on {challenge_name} and is now in {rank_str} place with {score} points!",
            "color": COLOR_SECOND_BLOOD,
        }
    elif blood_index == 2:
        return {
            "title": "Third Blood!",
            "description": f"{team_name} got third blood on {challenge_name} and is now in {rank_str} place with {score} points!",
            "color": COLOR_THIRD_BLOOD,
        }
    return {}


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
        with urllib.request.urlopen(req, timeout=8) as resp:
            pass
    except Exception as exc:
        log.warning("Failed to post blood notification to Discord webhook: %s", exc)


def notify(challenge, team, solve, earlier_count=None):
    """Check if this solve qualifies as 1st, 2nd, or 3rd blood, and send a Discord webhook embed."""
    if challenge is None or team is None or solve is None:
        return
    # Exclude hidden or banned teams (e.g. crew / test accounts)
    if getattr(team, "hidden", False) or getattr(team, "banned", False):
        return

    solve_id = getattr(solve, "id", None)
    if solve_id is not None:
        with _LOCK:
            if solve_id in _NOTIFIED_SOLVES:
                return
            _NOTIFIED_SOLVES.add(solve_id)

    webhook_url = get_webhook_url()
    if not webhook_url:
        return

    if earlier_count is None:
        from CTFd.models import Solves, Teams
        earlier_count = Solves.query.join(Teams, Solves.account_id == Teams.id).filter(
            Solves.challenge_id == challenge.id,
            Solves.id < solve.id,
            Teams.hidden == False,
            Teams.banned == False,
        ).count()

    if earlier_count not in (0, 1, 2):
        return  # 4th solve or later

    rank, score = get_team_rank_and_score(team)

    embed = create_blood_embed(
        blood_index=earlier_count,
        team_name=team.name,
        challenge_name=challenge.name,
        rank=rank,
        score=score,
    )

    if embed:
        payload_data = {"embeds": [embed]}
        t = threading.Thread(target=_send_webhook, args=(webhook_url, payload_data), daemon=True)
        t.start()
