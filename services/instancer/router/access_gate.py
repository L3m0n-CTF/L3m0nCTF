"""The crew-only gate on instance addresses.

Instruction of the owner, 21 Nov 2026: while the challenges are being tested, only the people the admins approved on crew.l3m0nctf.xyz may reach them. STRICT.

Cloudflare Access (the service that already guards crew.l3m0nctf.xyz) puts a signed token on every request it lets through: a JWT, RS256, in the header
Cf-Access-Jwt-Assertion and in the cookie CF_Authorization. The central router checks that token ITSELF before it looks at the instance, so the gate does not
depend on the Cloudflare dashboard being set up right: a request that did not come through Access, or that Access issued for another application or another
team, never reaches an instance, and an outsider cannot even tell whether an instance exists (the answer is 403 for every name).

Modes (INSTANCE_ACCESS_MODE):
  crew  the default. Every instance request needs a valid token for one of INSTANCE_ACCESS_AUD (the AUD tag of the crew application in Cloudflare Access) of
        the team INSTANCE_ACCESS_TEAM_DOMAIN. If either setting is missing the gate refuses EVERYTHING: a gate that cannot tell who may pass stays shut.
  open  no check. For the event, when the players must reach their instances.

Standard library only. RS256 is checked by re-encoding the block that a correct signature must contain and comparing it whole (no padding parser, so none of
the forgeries that parsers allowed); only RS256 is accepted (not "none", not HS256 with the public key as the secret); keys under 2048 bits are not trusted.
The signing keys are fetched from https://<team>.cloudflareaccess.com/cdn-cgi/access/certs, cached for an hour, refreshed when a token names a key we do not
know (at most once a minute, so random key ids cannot make the router fetch without end), and the keys already known keep working when a refresh fails.
"""
import asyncio
import base64
import hashlib
import hmac
import json
import logging
import re
import time
import urllib.request
from typing import Callable, Optional

_log = logging.getLogger("l3mon.instancer.router.access")

_SHA256_PREFIX = bytes.fromhex("3031300d060960864801650304020105000420")  # the DER prefix of a SHA-256 DigestInfo
MIN_MODULUS_BITS = 2048
KEYS_LIFETIME_SECONDS = 3600.0
KEYS_STALE_LIMIT_SECONDS = 24 * 3600.0  # keys older than this are dropped when they cannot be refreshed: after a day without Cloudflare the gate closes
UNKNOWN_KEY_REFRESH_INTERVAL_SECONDS = 60.0
REFRESH_FAILURE_BACKOFF_SECONDS = 30.0
CLOCK_LEEWAY_SECONDS = 30
TOKEN_CACHE_MAX = 4096
MAX_TOKEN_BYTES = 8192
_TEAM_DOMAIN = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}\.cloudflareaccess\.com\Z")


def _b64u_decode(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def rs256_verify(n: int, e: int, signed: bytes, signature: bytes) -> bool:
    """RSASSA-PKCS1-v1_5 with SHA-256, by re-encoding what the signature must decrypt to and comparing it whole."""
    k = (n.bit_length() + 7) // 8
    if len(signature) != k:
        return False
    s = int.from_bytes(signature, "big")
    if s >= n:
        return False
    em = pow(s, e, n).to_bytes(k, "big")
    t = _SHA256_PREFIX + hashlib.sha256(signed).digest()
    if k < len(t) + 11:
        return False
    expected = b"\x00\x01" + b"\xff" * (k - len(t) - 3) + b"\x00" + t
    return hmac.compare_digest(em, expected)


def _parse_keys(document: dict) -> dict[str, tuple[int, int]]:
    keys: dict[str, tuple[int, int]] = {}
    for jwk in (document or {}).get("keys", []) or []:
        try:
            if jwk.get("kty") != "RSA" or not isinstance(jwk.get("kid"), str):
                continue
            n = int.from_bytes(_b64u_decode(jwk["n"]), "big")
            e = int.from_bytes(_b64u_decode(jwk["e"]), "big")
        except (KeyError, ValueError, TypeError):
            continue
        if n.bit_length() < MIN_MODULUS_BITS or e < 3 or e % 2 == 0:
            continue
        keys[jwk["kid"]] = (n, e)
    return keys


class AccessGate:
    def __init__(
        self,
        mode: str,
        team_domain: str,
        audiences: list[str],
        fetch_jwks: Optional[Callable[[], dict]] = None,
        clock: Callable[[], float] = time.time,
    ):
        mode = (mode or "").strip().lower()
        if mode not in ("crew", "open"):
            raise ValueError(f"INSTANCE_ACCESS_MODE must be 'crew' or 'open', got {mode!r}")
        self.mode = mode
        self.team_domain = (team_domain or "").strip().lower()
        self.audiences = {a.strip() for a in (audiences or []) if a and a.strip()}
        self._clock = clock
        self._fetch = fetch_jwks or self._fetch_from_cloudflare
        # "crew" needs both a Cloudflare team name (checked: a name of ours is a request to that name) and at least one application tag
        self.configured = mode == "open" or (bool(_TEAM_DOMAIN.match(self.team_domain)) and bool(self.audiences))
        self._keys: dict[str, tuple[int, int]] = {}
        self._keys_loaded_at: Optional[float] = None
        self._last_unknown_key_refresh = float("-inf")
        self._next_refresh_allowed = float("-inf")
        self._verified: dict[str, float] = {}  # sha256 of a token that passed -> its expiry
        self.refusals: dict[str, int] = {}

    # ------------------------------------------------------------------ keys
    def _fetch_from_cloudflare(self) -> dict:
        req = urllib.request.Request(f"https://{self.team_domain}/cdn-cgi/access/certs", headers={"User-Agent": "l3mon-instance-router"})
        with urllib.request.urlopen(req, timeout=5) as resp:
            return json.loads(resp.read(262144))

    async def _refresh_keys(self) -> bool:
        now = self._clock()
        if now < self._next_refresh_allowed:
            return False
        try:
            document = await asyncio.get_running_loop().run_in_executor(None, self._fetch)
            keys = _parse_keys(document)
            if not keys:
                raise ValueError("the key document holds no usable RSA key")
        except Exception as e:  # the old keys stay in use, within their stale limit
            self._next_refresh_allowed = now + REFRESH_FAILURE_BACKOFF_SECONDS
            _log.warning("Could not fetch the Cloudflare Access signing keys: %s", e)
            if self._keys_loaded_at is not None and now - self._keys_loaded_at > KEYS_STALE_LIMIT_SECONDS:
                self._keys = {}
                self._keys_loaded_at = None
            return False
        self._keys, self._keys_loaded_at = keys, now
        return True

    async def _key_for(self, kid: str) -> Optional[tuple[int, int]]:
        now = self._clock()
        if self._keys_loaded_at is None or now - self._keys_loaded_at > KEYS_LIFETIME_SECONDS:
            await self._refresh_keys()
        if kid not in self._keys and self._keys_loaded_at is not None and now - self._last_unknown_key_refresh >= UNKNOWN_KEY_REFRESH_INTERVAL_SECONDS:
            self._last_unknown_key_refresh = now
            await self._refresh_keys()
        return self._keys.get(kid)

    # ------------------------------------------------------------------ the check
    @staticmethod
    def _token(headers: list[tuple[str, str]]) -> str:
        cookie_token = ""
        for k, v in headers:
            kl = k.lower()
            if kl == "cf-access-jwt-assertion" and v.strip():
                return v.strip()
            if kl == "cookie" and not cookie_token:
                for part in v.split(";"):
                    name, _, value = part.strip().partition("=")
                    if name == "CF_Authorization" and value:
                        cookie_token = value
                        break
        return cookie_token

    def _refuse(self, reason: str) -> tuple[bool, str]:
        self.refusals[reason] = self.refusals.get(reason, 0) + 1
        return False, reason

    async def check(self, headers: list[tuple[str, str]]) -> tuple[bool, str]:
        """(allowed, reason). The reason is for the log and the metrics, never for the visitor."""
        if self.mode == "open":
            return True, "open"
        if not self.configured:
            return self._refuse("gate not configured")
        token = self._token(headers)
        if not token:
            return self._refuse("no token")
        if len(token) > MAX_TOKEN_BYTES:
            return self._refuse("malformed token")
        now = self._clock()
        digest = hashlib.sha256(token.encode("utf-8", errors="replace")).hexdigest()
        cached_exp = self._verified.get(digest)
        if cached_exp is not None and cached_exp > now - CLOCK_LEEWAY_SECONDS:
            return True, "ok"
        parts = token.split(".")
        if len(parts) != 3:
            return self._refuse("malformed token")
        try:
            header = json.loads(_b64u_decode(parts[0]))
            payload = json.loads(_b64u_decode(parts[1]))
            signature = _b64u_decode(parts[2])
        except (ValueError, UnicodeDecodeError):
            return self._refuse("malformed token")
        if not isinstance(header, dict) or not isinstance(payload, dict):
            return self._refuse("malformed token")
        if header.get("alg") != "RS256":
            return self._refuse("algorithm not allowed")
        kid = header.get("kid")
        if not isinstance(kid, str) or not kid:
            return self._refuse("malformed token")
        if self._keys_loaded_at is None and not self._keys:
            await self._refresh_keys()
            if not self._keys:
                return self._refuse("keys unavailable")
        key = await self._key_for(kid)
        if key is None:
            return self._refuse("keys unavailable" if not self._keys else "unknown key")
        if not rs256_verify(key[0], key[1], f"{parts[0]}.{parts[1]}".encode("ascii", errors="replace"), signature):
            return self._refuse("bad signature")
        exp, nbf = payload.get("exp"), payload.get("nbf")
        if isinstance(exp, bool) or not isinstance(exp, (int, float)) or exp <= now - CLOCK_LEEWAY_SECONDS:
            return self._refuse("expired")
        if isinstance(nbf, (int, float)) and not isinstance(nbf, bool) and nbf > now + CLOCK_LEEWAY_SECONDS:
            return self._refuse("not yet valid")
        if payload.get("iss") != f"https://{self.team_domain}":
            return self._refuse("wrong issuer")
        aud = payload.get("aud")
        auds = {a for a in (aud if isinstance(aud, list) else [aud]) if isinstance(a, str)}
        if not (self.audiences & auds):
            return self._refuse("wrong audience")
        if len(self._verified) >= TOKEN_CACHE_MAX:
            self._verified = {d: x for d, x in self._verified.items() if x > now}
            if len(self._verified) >= TOKEN_CACHE_MAX:
                self._verified.clear()
        self._verified[digest] = float(exp)
        return True, "ok"
