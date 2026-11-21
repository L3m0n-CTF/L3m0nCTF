"""A young instance gets a moment to open its port; an old or removed one fails fast.

Found on the real challenge host on 21 Nov 2026:
 - the router started relaying to a TCP instance (and routing web requests) the moment the container existed, before the challenge had opened its port: a
   player who connected in the first second got an instant close (TCP) or a 502 (web) (router log: "TCP connect failure ... Connect call failed");
 - a request to an instance that had just been stopped (its network removed, so its address answers nothing at all) hung for the whole 30 s idle timeout
   before the router said 404.
"""
import asyncio
import socket
import time

import pytest

from services.instancer.router import router as router_module
from services.instancer.router.router import InstanceRouter

DOMAIN = "inst.test"
IID = "0123456789abcdef0123456789abcdef"


def free_port(count: int = 20) -> int:
    while True:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]
            if 1024 <= port <= 65535 - count:
                return port


async def http_ok(reader, writer):
    await reader.readuntil(b"\r\n\r\n")
    writer.write(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\nConnection: close\r\n\r\nok")
    await writer.drain()
    writer.close()


def refuse_until(monkeypatch, seconds: float):
    """Connection attempts to the instance are refused at once until `seconds` have passed (as a Linux host does for a port nobody listens on yet).
    Windows delays a refusal by about a second, which would hide the race these tests are about, so the refusal is made here on every system."""
    ready_at = time.time() + seconds
    real = asyncio.open_connection

    async def gated(host, port, **kw):
        if time.time() < ready_at:
            raise ConnectionRefusedError(111, "Connect call failed")
        return await real(host, port, **kw)

    monkeypatch.setattr(router_module.asyncio, "open_connection", gated)


def make_router(**kw) -> InstanceRouter:
    return InstanceRouter(instance_domain=DOMAIN, allow_loopback_for_tests=True, instance_pool="127.0.0.0/8", **kw)


@pytest.mark.asyncio
async def test_a_web_request_to_an_instance_that_is_still_starting_waits_for_it(monkeypatch):
    port = free_port()
    server = await asyncio.start_server(http_ok, "127.0.0.1", port)
    refuse_until(monkeypatch, 0.8)
    router = make_router()
    router.update_routes([{"id": IID, "protocol": "http", "target_ip": "127.0.0.1", "target_port": port, "created_at": time.time()}])
    try:
        status, _, body = await router.handle_http_request("GET", "/", {"Host": f"{IID}.{DOMAIN}"}, b"")
        assert status == 200 and body == b"ok", f"the router gave up on an instance that was only starting: {status}"
    finally:
        server.close()
        await server.wait_closed()


@pytest.mark.asyncio
async def test_an_instance_that_never_opens_its_port_is_given_up_on_after_the_grace_period(monkeypatch):
    monkeypatch.setattr(router_module, "READY_GRACE_SECONDS", 1.0)
    router = make_router()
    router.update_routes([{"id": IID, "protocol": "http", "target_ip": "127.0.0.1", "target_port": free_port(), "created_at": time.time()}])
    t0 = time.time()
    status, _, _ = await router.handle_http_request("GET", "/", {"Host": f"{IID}.{DOMAIN}"}, b"")
    took = time.time() - t0
    assert status == 502
    # it keeps trying until one retry delay (0.25 s) before the end of the grace period, so a grace of 1 s means 0.75 s or more of waiting
    assert 0.6 <= took < 4, f"waited {took:.1f} s: it must wait out the grace period (1 s here) and then answer 502"


@pytest.mark.asyncio
async def test_an_old_instance_that_does_not_answer_fails_fast_not_after_the_idle_timeout(monkeypatch):
    monkeypatch.setattr(router_module, "BACKEND_CONNECT_TIMEOUT", 0.5)

    async def hang(host, port, **kw):
        await asyncio.sleep(3600)

    monkeypatch.setattr(router_module.asyncio, "open_connection", hang)
    router = make_router()
    router.update_routes([{"id": IID, "protocol": "http", "target_ip": "127.0.0.1", "target_port": 9, "created_at": time.time() - 600}])
    t0 = time.time()
    status, _, _ = await router.handle_http_request("GET", "/", {"Host": f"{IID}.{DOMAIN}"}, b"")
    took = time.time() - t0
    assert status in (502, 504)
    assert took < 3, f"the visitor waited {took:.1f} s for an address that answers nothing (the old limit was 30 s)"


@pytest.mark.asyncio
async def test_a_route_without_a_start_time_gets_no_grace_period(monkeypatch):
    """Routes from an older agent have no created_at: they behave as before, one attempt (but with the short connect timeout)."""
    router = make_router()
    router.update_routes([{"id": IID, "protocol": "http", "target_ip": "127.0.0.1", "target_port": free_port()}])
    t0 = time.time()
    status, _, _ = await router.handle_http_request("GET", "/", {"Host": f"{IID}.{DOMAIN}"}, b"")
    assert status == 502 and time.time() - t0 < 3


@pytest.mark.asyncio
async def test_a_tcp_relay_to_an_instance_that_is_still_starting_waits_for_it(monkeypatch):
    relay, target = free_port(), free_port()
    router = make_router(tcp_port_start=relay, tcp_port_count=5)
    received = []

    async def echo(reader, writer):
        data = await reader.read(100)
        received.append(data)
        writer.write(b"hello:" + data)
        await writer.drain()
        writer.close()

    server = await asyncio.start_server(echo, "127.0.0.1", target)
    real_open = asyncio.open_connection  # the player's own connection must not be refused by the gate that stands in for the not-yet-open instance
    refuse_until(monkeypatch, 0.8)
    try:
        await router.sync_tcp_routes([{"id": IID, "protocol": "tcp", "port": relay, "target_ip": "127.0.0.1", "target_port": target, "created_at": time.time()}])
        r, w = await real_open("127.0.0.1", relay)
        w.write(b"ping")
        await w.drain()
        answer = await asyncio.wait_for(r.read(100), 6)
        assert answer == b"hello:ping", f"the relay dropped a player who connected while the instance was starting: {answer!r}"
        w.close()
    finally:
        await router.close_all_tcp_listeners()
        server.close()
        await server.wait_closed()
