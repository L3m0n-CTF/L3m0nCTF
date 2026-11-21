"""The platform's client to the challenge host: a slow answer is not a dead host.

Found on the real challenge host on 21 Nov 2026: 80 teams starting at the same instant took 28 s in all (the median start 16 s) while the platform's client
waited 10 s for ANY call, and treated a timeout as a host failure ("upstream_unavailable"). The plugin then marked the only host down for 30 s, which refuses
everybody's starts and shows every running instance as "error": a slow moment became a 30 second outage.

Now: a start has its own, longer timeout (45 s, L3MON_INSTANCER_START_TIMEOUT), and a timeout is reported as its own kind of error ("timeout", 504), so that the
plugin can tell a slow host from an unreachable one (api.py, hosts.py).

This file loads plugins/l3mon_instances/client.py by its path: the client uses only the standard library, so it needs no CTFd (and it lives here, with the
instancer's own tests, because a test inside the plugin's folder cannot even be set up without CTFd).
"""
import http.server
import importlib.util
import json
import socket
import threading
import time
from pathlib import Path

import pytest

CLIENT_PY = Path(__file__).resolve().parents[3] / "plugins" / "l3mon_instances" / "client.py"


def load_client():
    spec = importlib.util.spec_from_file_location("l3mon_instances_client_under_test", CLIENT_PY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class Handler(http.server.BaseHTTPRequestHandler):
    delay = 0.0

    def _reply(self, payload):
        body = json.dumps(payload).encode()
        self.send_response(201 if self.command == "POST" else 200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        self.rfile.read(int(self.headers.get("Content-Length", "0") or 0))
        time.sleep(type(self).delay)
        self._reply({"instance": {"id": "a" * 32, "state": "running"}})

    def do_GET(self):
        time.sleep(type(self).delay)
        self._reply({"slots_total": 10, "slots_used": 0})

    def log_message(self, *a):
        pass


@pytest.fixture
def slow_server():
    Handler.delay = 0.0
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}", Handler
    server.shutdown()
    server.server_close()


def test_a_start_that_outlasts_its_timeout_is_a_timeout_not_an_unreachable_host(slow_server):
    mod = load_client()
    url, handler = slow_server
    handler.delay = 1.0
    client = mod.InstancerClient(base_url=url, token="t", timeout=10, start_timeout=0.3)
    with pytest.raises(mod.InstancerClientError) as e:
        client.start_instance(1, 1, "demo", "L3m0" + "n{" + "0" * 32 + "}")
    assert e.value.error_code == "timeout" and e.value.status == 504, (e.value.error_code, e.value.status)


def test_a_refused_connection_is_still_an_unreachable_host():
    mod = load_client()
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        closed = s.getsockname()[1]
    # (Windows reports a refused connection only after about 2 s, so the timeout is generous here: this test is about WHICH error, not how fast)
    client = mod.InstancerClient(base_url=f"http://127.0.0.1:{closed}", token="t", timeout=10, start_timeout=10)
    with pytest.raises(mod.InstancerClientError) as e:
        client.start_instance(1, 1, "demo", "x")
    assert e.value.error_code == "upstream_unavailable" and e.value.status == 503


def test_a_start_gets_the_long_timeout_and_the_other_calls_keep_theirs(slow_server):
    mod = load_client()
    url, handler = slow_server
    handler.delay = 0.6
    client = mod.InstancerClient(base_url=url, token="t", timeout=0.2, start_timeout=5)
    assert client.start_instance(1, 1, "demo", "x")["id"] == "a" * 32, "the start waits out the 0.6 s answer: its timeout is 5 s"
    with pytest.raises(mod.InstancerClientError) as e:
        client.get_capacity()
    assert e.value.error_code == "timeout", "a capacity probe keeps the short timeout and reports a timeout as such"


def test_the_start_timeout_is_45_seconds_unless_the_environment_says_otherwise(monkeypatch):
    mod = load_client()
    monkeypatch.delenv("L3MON_INSTANCER_START_TIMEOUT", raising=False)
    assert mod.InstancerClient(base_url="http://x", token="t").start_timeout == 45
    monkeypatch.setenv("L3MON_INSTANCER_START_TIMEOUT", "60")
    assert mod.InstancerClient(base_url="http://x", token="t").start_timeout == 60
    monkeypatch.setenv("L3MON_INSTANCER_START_TIMEOUT", "nonsense")
    assert mod.InstancerClient(base_url="http://x", token="t").start_timeout == 45, "a bad value falls back to the default, it must not break starts"
    assert mod.InstancerClient(base_url="http://x", token="t", start_timeout=7).start_timeout == 7
