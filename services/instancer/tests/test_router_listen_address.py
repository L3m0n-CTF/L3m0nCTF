"""The host router must not listen on the networks of the instances it serves.

Found on the real challenge host on 21 Nov 2026 (services/instancer/tools/real_host_proof.py, group "isolation"): the router container is attached to the
private network of every instance, so that it can reach the instance, and it listened on 0.0.0.0. From inside team A's instance the router's address on A's
network answered on its HTTP port (a request with another instance's id in the Host header returned that instance's page) and on every one of the 200 TCP
relay ports (the banner of team B's TCP instance came back). A player with code execution in one instance could therefore attack every TCP instance, and
read any instance whose 32-character id they knew.

The router now listens only on its address on the trusted network (the one that reaches the agent), so the instance networks have nothing to connect to.
"""
import asyncio
import socket

import pytest

from services.instancer.router.main import parse_router_config, resolve_listen_addr, start_http_listener
from services.instancer.router.router import InstanceRouter, local_address_towards


def free_port(count: int = 20) -> int:
    while True:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]
            if 1024 <= port <= 65535 - count:
                return port


def make_router(port: int, **kw) -> InstanceRouter:
    return InstanceRouter(
        instance_domain="example.com",
        instancer_url="http://127.0.0.1:8300",
        router_token="secret",
        tcp_port_start=port,
        tcp_port_count=10,
        instance_pool="127.0.0.0/8",
        allow_loopback_for_tests=True,
        **kw,
    )


@pytest.mark.asyncio
async def test_tcp_relay_listeners_bind_only_to_the_listen_address():
    port, backend_port = free_port(), free_port()
    backend = await asyncio.start_server(lambda r, w: w.close(), "127.0.0.1", backend_port)
    router = make_router(port, listen_addr="127.0.0.1")
    try:
        await router.sync_tcp_routes([{"id": "i1", "protocol": "tcp", "port": port, "target_ip": "127.0.0.1", "target_port": backend_port}])
        bound = [s.getsockname()[0] for s in router._tcp_listeners[port].sockets]
        assert bound == ["127.0.0.1"], f"the relay listens on {bound}, not only on the trusted address"
    finally:
        await router.close_all_tcp_listeners()
        backend.close()
        await backend.wait_closed()


def test_the_default_listen_address_of_a_router_object_is_unchanged_for_existing_callers():
    assert make_router(free_port()).listen_addr == "0.0.0.0"


@pytest.mark.asyncio
async def test_the_http_listener_binds_to_the_listen_address():
    router = make_router(free_port(), listen_addr="127.0.0.1")
    port = free_port()
    server = await start_http_listener(router, "127.0.0.1", port)
    try:
        assert [s.getsockname()[0] for s in server.sockets] == ["127.0.0.1"]
    finally:
        server.close()
        await server.wait_closed()


def test_local_address_towards_a_loopback_url_is_loopback():
    assert local_address_towards("http://127.0.0.1:8300") == "127.0.0.1"


def test_local_address_towards_a_name_that_does_not_resolve_is_an_error_not_a_wildcard():
    with pytest.raises(ValueError, match="does not resolve"):
        local_address_towards("http://no-such-host.invalid:8300", attempts=1, delay=0)


def test_config_default_is_auto_for_a_host_router_and_all_interfaces_for_the_central_router():
    assert parse_router_config({})["listen_addr"] == "auto"
    assert parse_router_config({"ROUTER_HOSTS_PATH": "/etc/l3mon/hosts.json"})["listen_addr"] == "0.0.0.0"
    assert parse_router_config({"ROUTER_LISTEN_ADDR": "10.9.8.7"})["listen_addr"] == "10.9.8.7"


def test_resolve_listen_addr():
    assert resolve_listen_addr({"listen_addr": "10.9.8.7", "instancer_url": "http://x:1"}) == "10.9.8.7"
    assert resolve_listen_addr({"listen_addr": "0.0.0.0", "instancer_url": "http://x:1"}) == "0.0.0.0"
    assert resolve_listen_addr({"listen_addr": "auto", "instancer_url": "http://127.0.0.1:8300"}) == "127.0.0.1"
    with pytest.raises(ValueError, match="ROUTER_LISTEN_ADDR"):
        resolve_listen_addr({"listen_addr": "not-an-address", "instancer_url": "http://x:1"})
