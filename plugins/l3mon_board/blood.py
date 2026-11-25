"""Discord notifications for first, second, and third blood on challenge solves.

Webhook:
  Configured by L3MON_DISCORD_BLOOD_WEBHOOK or DISCORD_BLOOD_WEBHOOK environment variable.
  Defaults to the official CTF Discord BloodSeeker webhook.
"""
import json
import logging
import os
import random
import threading
import urllib.request

log = logging.getLogger("l3mon.blood")

DEFAULT_WEBHOOK = "https://discord.com/api/webhooks/1449642076215181464/dRFI-C8I-nEvSSIbdTgdbzcwZ7fWe_wze784J5n3z4ICDskCEaddRUaFB_PzqZ6TPBhn"
_LOCK = threading.Lock()
_NOTIFIED_SOLVES = set()


def get_webhook_url() -> str:
    url = os.environ.get("L3MON_DISCORD_BLOOD_WEBHOOK") or os.environ.get("DISCORD_BLOOD_WEBHOOK") or DEFAULT_WEBHOOK
    url = url.strip()
    if url.lower() in ("", "none", "false", "0", "off"):
        return ""
    return url


def ordinal(n) -> str:
    if n is None:
        return "1st"
    try:
        n = int(n)
    except (ValueError, TypeError):
        return f"{n}th"
    if 11 <= (n % 100) <= 13:
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


def get_team_rank_and_score(team):
    score = 0
    rank = None
    try:
        score = int(team.get_score(admin=True) or 0)
    except Exception:
        pass
    try:
        rank = team.get_place(admin=True)
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


def format_blood_message(blood_index: int, team_name: str, challenge_name: str, rank: int, score: int, challenge_value: int) -> str:
    """Format the blood notification message.
    blood_index: 0 for First Blood, 1 for Second Blood, 2 for Third Blood.
    """
    rank_str = ordinal(rank)
    points_str = f"{score:,}"
    earned_str = f"{challenge_value:,}" if challenge_value else points_str

    if blood_index == 0:
        return (
            "🩸 First Blood\n"
            "First Blood claimed!\n\n"
            f"Congratulations to {team_name} for being the first to solve {challenge_name}!\n"
            f"They now hold {rank_str} place on the leaderboard with {points_str} points."
        )
    elif blood_index == 1:
        if random.random() < 0.5:
            return (
                "🩸 Second Blood\n"
                "Second Blood secured!\n\n"
                f"Well played, {team_name}, for being the second solver of {challenge_name}!\n"
                f"They have climbed to {rank_str} place with an impressive {points_str} points."
            )
        else:
            return (
                "🩸 Second Blood\n"
                "Another Second Blood!\n\n"
                f"{team_name} secured the second solve on {challenge_name} and currently stand at {rank_str} place with {points_str} points."
            )
    elif blood_index == 2:
        return (
            "🩸 Third Blood\n"
            "Third Blood claimed!\n\n"
            f"The race continues! {team_name} was the third team to solve {challenge_name}, earning {earned_str} points and securing {rank_str} place on the leaderboard."
        )
    return ""


def _send_webhook(url: str, content: str):
    try:
        payload = json.dumps({"content": content}).encode("utf-8")
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
    """Check if this solve qualifies as 1st, 2nd, or 3rd blood, and send a Discord webhook notification."""
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
    challenge_val = getattr(challenge, "value", None)
    if challenge_val is None:
        try:
            challenge_val = int(challenge.value or 0)
        except Exception:
            challenge_val = 0

    message = format_blood_message(
        blood_index=earlier_count,
        team_name=team.name,
        challenge_name=challenge.name,
        rank=rank,
        score=score,
        challenge_value=challenge_val,
    )

    if message:
        t = threading.Thread(target=_send_webhook, args=(webhook_url, message), daemon=True)
        t.start()
