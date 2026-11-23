"""How long a challenge instance lives (owner's rule of 23 Nov 2026: 15 minutes, then the participant starts a new one).

INSTANCER_MAX_TTL_MINUTES (default 15, 1 to 180): the longest any instance lives, whatever its challenge's manifest says (a challenge that asks for less keeps
its own shorter time). INSTANCER_MAX_RENEWALS (default 0, 0 to 100): how many times a studio may renew one instance; 0 means an instance cannot be extended.
A setting that makes no sense stops the agent at start with its name in the message, never a silent fallback.
"""
import os

GLOBAL_MAX_TTL_MINUTES = 180  # the manifest's own bound (manifest.py) and the largest ceiling an operator can set
DEFAULT_MAX_TTL_MINUTES = 15
DEFAULT_MAX_RENEWALS = 0
MAX_RENEWALS_LIMIT = 100


class RenewLimitError(ValueError):
    """A renewal the host does not allow (the studio has used the renewals it may). A ValueError so that a caller written before this class still catches it."""


def _whole(env, name, default, low, high) -> int:
    raw = str(env.get(name, "")).strip()
    if not raw:
        return default
    try:
        number = int(raw)
    except ValueError:
        raise ValueError(f"{name} must be a whole number, got {raw!r}") from None
    if not (low <= number <= high):
        raise ValueError(f"{name} must be between {low} and {high}, got {number}")
    return number


def read_instance_lifetime(env=None) -> tuple[int, int]:
    """(the ceiling on an instance's lifetime in minutes, the renewals a studio may use) from the environment, checked."""
    e = os.environ if env is None else env
    ttl = _whole(e, "INSTANCER_MAX_TTL_MINUTES", DEFAULT_MAX_TTL_MINUTES, 1, GLOBAL_MAX_TTL_MINUTES)
    renewals = _whole(e, "INSTANCER_MAX_RENEWALS", DEFAULT_MAX_RENEWALS, 0, MAX_RENEWALS_LIMIT)
    return ttl, renewals
