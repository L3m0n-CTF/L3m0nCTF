"""The scheme of the address a player is given for a web instance.

A real domain (`l3m0nctf.xyz`) sits behind Cloudflare, which serves a visitor on 443 and nothing useful on 80, so the address must be https. A development
address (`localhost:8090`, an address with a port) is the router itself, which speaks plain http. `INSTANCE_URL_SCHEME` (http or https) overrides the guess.
"""
from typing import Optional

_SCHEMES = ("http", "https")


def instance_url_scheme(instance_domain: str, override: Optional[str] = None) -> str:
    """-> "https" or "http". `override` (the INSTANCE_URL_SCHEME setting) wins when given; anything but http or https is refused."""
    setting = (override or "").strip().lower()
    if setting:
        if setting not in _SCHEMES:
            raise ValueError(f"INSTANCE_URL_SCHEME must be http or https, not {override!r}")
        return setting
    domain = (instance_domain or "").strip().lower()
    bare = domain.rsplit("]", 1)[-1] if domain.startswith("[") else domain
    if ":" in bare or domain.startswith("[") or domain == "localhost" or domain.endswith(".localhost"):
        return "http"
    return "https"
