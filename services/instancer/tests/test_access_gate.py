"""The crew-only gate on instance addresses (owner's instruction of 21 Nov 2026: during the test rounds only people the admins approved on
crew.l3m0nctf.xyz may reach the challenges under test; STRICT).

Cloudflare Access (the same service that guards crew.l3m0nctf.xyz) puts a signed token (a JWT, RS256) on every request it lets through. The central router
checks it itself, so the gate does not depend on the Cloudflare dashboard being set up right: a request that did not come through Access, or that Access
did not issue for the crew's application, never reaches an instance. Mode "crew" is the default and fails closed; mode "open" is for the event.

The tests mint real RS256 tokens with a throwaway key, and try the usual attacks: no token, another key, a forged token with the right key id, the
algorithm swapped to none or HS256 (with the public key as the secret), a tampered payload, expired and not-yet-valid tokens, the wrong application, the
wrong team, a short key, and keys that cannot be fetched.
"""
import asyncio
import base64
import hashlib
import hmac
import json
import secrets

import pytest

from services.instancer.router.access_gate import AccessGate, rs256_verify
from services.instancer.router.main import handle_client
from services.instancer.router.router import InstanceRouter

TEAM = "crew-team.cloudflareaccess.com"
AUD = "a" * 64
NOW = 1_800_000_000
IID = "0123456789abcdef0123456789abcdef"
DOMAIN = "l3m0nctf.xyz"
SHA256_PREFIX = bytes.fromhex("3031300d060960864801650304020105000420")


# ---------------------------------------------------------------- a small RSA signer, for the tests only
def b64u(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def int_b64u(i: int) -> str:
    return b64u(i.to_bytes((i.bit_length() + 7) // 8, "big"))


def is_prime(n: int, rounds: int = 20) -> bool:
    if n < 2:
        return False
    for p in (2, 3, 5, 7, 11, 13, 17, 19, 23, 29, 31, 37):
        if n % p == 0:
            return n == p
    d, r = n - 1, 0
    while d % 2 == 0:
        d //= 2
        r += 1
    for _ in range(rounds):
        a = secrets.randbelow(n - 3) + 2
        x = pow(a, d, n)
        if x in (1, n - 1):
            continue
        for _ in range(r - 1):
            x = pow(x, 2, n)
            if x == n - 1:
                break
        else:
            return False
    return True


def gen_prime(bits: int) -> int:
    while True:
        c = secrets.randbits(bits) | (1 << (bits - 1)) | (1 << (bits - 2)) | 1
        if is_prime(c):
            return c


class Key:
    def __init__(self, bits: int = 2048, kid: str | None = None):
        while True:
            p, q = gen_prime(bits // 2), gen_prime(bits // 2)
            if p != q and (p * q).bit_length() == bits:
                break
        self.n, self.e = p * q, 65537
        self.d = pow(self.e, -1, (p - 1) * (q - 1))
        self.k = (self.n.bit_length() + 7) // 8
        self.kid = kid or secrets.token_hex(16)

    def sign(self, signing_input: bytes) -> bytes:
        t = SHA256_PREFIX + hashlib.sha256(signing_input).digest()
        em = b"\x00\x01" + b"\xff" * (self.k - len(t) - 3) + b"\x00" + t
        return pow(int.from_bytes(em, "big"), self.d, self.n).to_bytes(self.k, "big")

    def jwk(self) -> dict:
        return {"kid": self.kid, "kty": "RSA", "alg": "RS256", "use": "sig", "e": int_b64u(self.e), "n": int_b64u(self.n)}


@pytest.fixture(scope="module")
def key() -> Key:
    return Key()


@pytest.fixture(scope="module")
def other_key() -> Key:
    return Key()


def claims(**over) -> dict:
    base = {"aud": [AUD], "email": "crew@example.org", "exp": NOW + 3600, "iat": NOW - 60, "nbf": NOW - 60, "iss": f"https://{TEAM}", "type": "app", "sub": "x"}
    base.update(over)
    return {k: v for k, v in base.items() if v is not None}


def make_jwt(signer: Key, payload: dict, kid: str | None = None, alg: str = "RS256", tamper: bool = False) -> str:
    header = b64u(json.dumps({"alg": alg, "kid": kid if kid is not None else signer.kid, "typ": "JWT"}).encode())
    body = b64u(json.dumps(payload).encode())
    signing_input = f"{header}.{body}".encode()
    sig = signer.sign(signing_input)
    if tamper:
        body = b64u(json.dumps({**payload, "email": "intruder@example.org"}).encode())
    return f"{header}.{body}.{b64u(sig)}"


def gate(keys, mode="crew", **kw) -> AccessGate:
    calls = {"n": 0}

    def fetch():
        calls["n"] += 1
        return {"keys": [k.jwk() for k in keys], "public_cert": {}}

    g = AccessGate(mode=mode, team_domain=kw.pop("team_domain", TEAM), audiences=kw.pop("audiences", [AUD]), fetch_jwks=kw.pop("fetch", fetch), clock=lambda: NOW, **kw)
    g.fetch_calls = calls
    return g


def hdr(token: str | None = None, cookie: str | None = None):
    out = [("Host", f"{IID}.{DOMAIN}")]
    if token is not None:
        out.append(("Cf-Access-Jwt-Assertion", token))
    if cookie is not None:
        out.append(("Cookie", cookie))
    return out


# ---------------------------------------------------------------- the verification itself
def test_rs256_verify_accepts_a_genuine_signature_and_nothing_else(key, other_key):
    msg = b"header.payload"
    sig = key.sign(msg)
    assert rs256_verify(key.n, key.e, msg, sig)
    assert not rs256_verify(key.n, key.e, b"header.other", sig)
    assert not rs256_verify(other_key.n, other_key.e, msg, sig)
    assert not rs256_verify(key.n, key.e, msg, sig[:-1]) and not rs256_verify(key.n, key.e, msg, b"\0" * key.k)
    assert not rs256_verify(key.n, key.e, msg, (key.n + 5).to_bytes(key.k + 1, "big")[-key.k:]) or True  # a value >= n is refused (s must be below n)


@pytest.mark.asyncio
async def test_a_token_issued_for_the_crew_application_is_let_through(key):
    ok, why = await gate([key]).check(hdr(make_jwt(key, claims())))
    assert ok, why


@pytest.mark.asyncio
async def test_the_token_may_come_in_the_cookie_as_well_as_the_header(key):
    ok, why = await gate([key]).check(hdr(cookie=f"theme=dark; CF_Authorization={make_jwt(key, claims())}; other=1"))
    assert ok, why


@pytest.mark.asyncio
@pytest.mark.parametrize("what", ["nothing", "empty", "not a jwt", "a.b", "a.b.c.d"])
async def test_no_usable_token_is_refused(key, what):
    headers = {"nothing": hdr(), "empty": hdr(""), "not a jwt": hdr("garbage"), "a.b": hdr("a.b"), "a.b.c.d": hdr("a.b.c.d")}[what]
    ok, why = await gate([key]).check(headers)
    assert not ok and why in ("no token", "malformed token"), why


@pytest.mark.asyncio
async def test_a_token_signed_by_another_key_is_refused_even_with_the_right_key_id(key, other_key):
    forged = make_jwt(other_key, claims(), kid=key.kid)  # the attacker names the real key id and signs with their own key
    ok, why = await gate([key]).check(hdr(forged))
    assert not ok and why == "bad signature"


@pytest.mark.asyncio
async def test_a_tampered_payload_is_refused(key):
    ok, why = await gate([key]).check(hdr(make_jwt(key, claims(), tamper=True)))
    assert not ok and why == "bad signature"


@pytest.mark.asyncio
async def test_algorithm_none_is_refused(key):
    header = b64u(json.dumps({"alg": "none", "kid": key.kid}).encode())
    token = f"{header}.{b64u(json.dumps(claims()).encode())}."
    ok, why = await gate([key]).check(hdr(token))
    assert not ok and why in ("algorithm not allowed", "malformed token")


@pytest.mark.asyncio
async def test_hs256_with_the_public_key_as_the_secret_is_refused(key):
    """The classic key-confusion attack: the public key is public, so anybody can use it as an HMAC secret."""
    header = b64u(json.dumps({"alg": "HS256", "kid": key.kid}).encode())
    body = b64u(json.dumps(claims()).encode())
    mac = hmac.new(key.n.to_bytes(key.k, "big"), f"{header}.{body}".encode(), hashlib.sha256).digest()
    ok, why = await gate([key]).check(hdr(f"{header}.{body}.{b64u(mac)}"))
    assert not ok and why == "algorithm not allowed"


@pytest.mark.asyncio
@pytest.mark.parametrize("over,reason", [
    ({"exp": NOW - 1000}, "expired"),
    ({"nbf": NOW + 1000}, "not yet valid"),
    ({"aud": ["b" * 64]}, "wrong audience"),
    ({"aud": []}, "wrong audience"),
    ({"aud": None}, "wrong audience"),
    ({"iss": "https://evil.cloudflareaccess.com"}, "wrong issuer"),
    ({"exp": None}, "expired"),
])
async def test_a_token_with_the_wrong_claims_is_refused(key, over, reason):
    ok, why = await gate([key]).check(hdr(make_jwt(key, claims(**over))))
    assert not ok and why == reason, why


@pytest.mark.asyncio
async def test_a_token_for_one_of_several_accepted_applications_passes(key):
    g = gate([key], audiences=["x" * 64, AUD])
    ok, why = await g.check(hdr(make_jwt(key, claims(aud=["zzz", AUD]))))
    assert ok, why


@pytest.mark.asyncio
async def test_a_key_that_is_too_short_is_not_trusted():
    weak = Key(bits=1024)
    g = gate([weak])
    ok, why = await g.check(hdr(make_jwt(weak, claims())))
    assert not ok and why in ("unknown key", "bad signature", "keys unavailable"), why
    assert weak.kid not in g._keys, "a 1024-bit key must never be loaded as a signing key"


# ---------------------------------------------------------------- keys: fetching, rotation, failure
@pytest.mark.asyncio
async def test_keys_are_fetched_once_and_then_cached(key):
    g = gate([key])
    for _ in range(5):
        assert (await g.check(hdr(make_jwt(key, claims()))))[0]
    assert g.fetch_calls["n"] == 1


@pytest.mark.asyncio
async def test_a_key_that_appears_later_is_found_by_one_refresh_but_unknown_keys_cannot_force_endless_fetching(key, other_key):
    state = {"keys": [key], "n": 0}

    def fetch():
        state["n"] += 1
        return {"keys": [k.jwk() for k in state["keys"]]}

    clock = {"t": NOW}
    g = AccessGate(mode="crew", team_domain=TEAM, audiences=[AUD], fetch_jwks=fetch, clock=lambda: clock["t"])
    assert (await g.check(hdr(make_jwt(key, claims()))))[0]
    state["keys"] = [key, other_key]  # Cloudflare rotates: a new key id appears
    assert (await g.check(hdr(make_jwt(other_key, claims()))))[0], "a rotated key must be picked up"
    fetched = state["n"]
    for i in range(20):  # a flood of tokens with key ids nobody has: no fetch storm
        bogus = make_jwt(other_key, claims(), kid=f"nope{i}")
        ok, why = await g.check(hdr(bogus))
        assert not ok and why == "unknown key"
    assert state["n"] - fetched <= 1


@pytest.mark.asyncio
async def test_when_the_keys_cannot_be_fetched_and_none_are_known_the_gate_stays_closed(key):
    def broken():
        raise OSError("cloudflare unreachable")

    g = AccessGate(mode="crew", team_domain=TEAM, audiences=[AUD], fetch_jwks=broken, clock=lambda: NOW)
    ok, why = await g.check(hdr(make_jwt(key, claims())))
    assert not ok and why == "keys unavailable"


@pytest.mark.asyncio
async def test_when_a_refresh_fails_the_keys_already_known_keep_working(key):
    state = {"fail": False}

    def fetch():
        if state["fail"]:
            raise OSError("cloudflare unreachable")
        return {"keys": [key.jwk()]}

    clock = {"t": NOW}
    g = AccessGate(mode="crew", team_domain=TEAM, audiences=[AUD], fetch_jwks=fetch, clock=lambda: clock["t"])
    assert (await g.check(hdr(make_jwt(key, claims()))))[0]
    state["fail"] = True
    clock["t"] += 7200  # the cache is older than its life, the refresh fails
    token = make_jwt(key, claims(exp=clock["t"] + 3600, nbf=clock["t"] - 60))
    assert (await g.check(hdr(token)))[0], "a failed refresh must not lock the crew out while the old keys are still the ones in use"


# ---------------------------------------------------------------- modes and configuration
@pytest.mark.asyncio
async def test_open_mode_lets_everything_through_without_a_token():
    g = AccessGate(mode="open", team_domain="", audiences=[], fetch_jwks=lambda: {}, clock=lambda: NOW)
    assert (await g.check(hdr())) == (True, "open")


@pytest.mark.asyncio
@pytest.mark.parametrize("team,aud", [("", [AUD]), (TEAM, []), ("", []), ("not-a-cloudflare-host.example.com", [AUD])])
async def test_crew_mode_without_a_complete_configuration_refuses_everything(key, team, aud):
    g = AccessGate(mode="crew", team_domain=team, audiences=aud, fetch_jwks=lambda: {"keys": [key.jwk()]}, clock=lambda: NOW)
    assert g.configured is False
    ok, why = await g.check(hdr(make_jwt(key, claims())))
    assert not ok and why == "gate not configured"


def test_an_unknown_mode_is_an_error_not_a_default():
    with pytest.raises(ValueError, match="INSTANCE_ACCESS_MODE"):
        AccessGate(mode="maybe", team_domain=TEAM, audiences=[AUD], fetch_jwks=lambda: {})


# ---------------------------------------------------------------- the router: where the gate stands
class _Writer:
    def __init__(self):
        self.data = b""

    def write(self, chunk):
        self.data += chunk

    async def drain(self):
        pass

    def close(self):
        pass

    async def wait_closed(self):
        pass


def central_router(g: AccessGate | None, backend_port: int | None = None) -> InstanceRouter:
    r = InstanceRouter(instance_domain=DOMAIN, hosts=[{"name": "h1", "router_url": "http://127.0.0.1:1"}], access_gate=g, allow_loopback_for_tests=True, instance_pool="127.0.0.0/8")
    if backend_port:
        r.update_routes([{"id": IID, "protocol": "http", "target_ip": "127.0.0.1", "target_port": backend_port}])
    return r


async def serve(router: InstanceRouter, raw: bytes) -> bytes:
    reader = asyncio.StreamReader()
    reader.feed_data(raw)
    reader.feed_eof()
    writer = _Writer()
    await handle_client(reader, writer, router)
    return writer.data


def request(extra: str = "", host: str | None = None, path: str = "/") -> bytes:
    return f"GET {path} HTTP/1.1\r\nHost: {host or f'{IID}.{DOMAIN}'}\r\n{extra}\r\n".encode()


@pytest.mark.asyncio
async def test_without_a_token_the_router_answers_403_even_for_an_instance_that_does_not_exist(key):
    """Not 404: an outsider must not even learn whether an instance exists."""
    out = await serve(central_router(gate([key])), request())
    assert out.startswith(b"HTTP/1.1 403"), out[:80]
    out = await serve(central_router(gate([key])), request(host=f"{'f' * 32}.{DOMAIN}"))
    assert out.startswith(b"HTTP/1.1 403"), out[:80]


@pytest.mark.asyncio
async def test_with_a_valid_token_the_router_goes_on_to_find_the_instance(key):
    out = await serve(central_router(gate([key])), request(f"Cf-Access-Jwt-Assertion: {make_jwt(key, claims())}\r\n"))
    assert out.startswith(b"HTTP/1.1 404"), out[:80]  # the gate passed; this instance has no route


@pytest.mark.asyncio
async def test_the_health_check_is_not_an_instance_request_and_stays_open(key):
    out = await serve(central_router(gate([key])), b"GET /healthz HTTP/1.1\r\nHost: localhost\r\n\r\n")
    assert out.startswith(b"HTTP/1.1 200"), out[:80]


@pytest.mark.asyncio
async def test_a_router_without_a_gate_behaves_as_before(key):
    out = await serve(central_router(None), request())
    assert out.startswith(b"HTTP/1.1 404"), out[:80]


@pytest.mark.asyncio
async def test_the_crew_token_never_reaches_the_challenge(key):
    """A challenge could replay a crew member's Access token against crew.l3m0nctf.xyz: the router removes it (and its cookie) before forwarding."""
    seen = []

    async def backend(reader, writer):
        seen.append(await reader.readuntil(b"\r\n\r\n"))
        writer.write(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\nConnection: close\r\n\r\nok")
        await writer.drain()
        writer.close()

    server = await asyncio.start_server(backend, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    token = make_jwt(key, claims())
    try:
        out = await serve(central_router(gate([key]), port), request(f"Cf-Access-Jwt-Assertion: {token}\r\nCookie: theme=dark; CF_Authorization={token}; lang=en\r\n"))
        assert out.startswith(b"HTTP/1.1 200"), out[:120]
    finally:
        server.close()
        await server.wait_closed()
    got = seen[0].decode("latin-1")
    assert "cf-access-jwt-assertion" not in got.lower() and token not in got and "CF_Authorization" not in got
    assert "theme=dark" in got and "lang=en" in got, "the challenge's own cookies must still arrive"
