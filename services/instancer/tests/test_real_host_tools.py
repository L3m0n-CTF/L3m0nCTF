"""The real-host proof tool, its preparation script and the stress fixture are test equipment that only runs on a challenge host. A mistake in them would be
found there, at the worst moment, so CI checks that they load, that their groups are what the guide promises, and that the fixture answers."""
import http.client
import importlib.util
import json
import os
import shutil
import socket
import subprocess
import threading
from pathlib import Path

import pytest

TOOLS = Path(__file__).resolve().parents[1] / "tools"
FIXTURE = Path(__file__).resolve().parent / "fixtures" / "stress" / "server.py"


def load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_the_proof_tool_loads_and_lists_the_groups_the_guide_names():
    proof = load(TOOLS / "real_host_proof.py", "real_host_proof")
    expected = {"auth", "input", "web", "hardening", "isolation", "lifecycle", "expiry", "tcp", "limits", "crash", "restart", "concurrency", "killall", "leaks", "capacity"}
    assert set(proof.GROUPS) == expected
    assert set(proof.HEAVY) == {"capacity"}, "the packing test fills the host: it runs only when asked for by name"
    flag = proof.new_flag()
    assert flag.startswith("L3m0" + "n{") and flag.endswith("}") and len(flag) == 6 + 32 + 1


def test_the_proof_tool_never_uses_a_team_number_a_real_team_could_have():
    proof = load(TOOLS / "real_host_proof.py", "real_host_proof2")
    assert proof.TEAM_BASE >= 910000, "test teams are numbered 910000 and up"


@pytest.mark.skipif(shutil.which("bash") is None, reason="no bash")
def test_the_preparation_script_has_no_syntax_error():
    # a relative path from the folder itself: on Windows `bash` can be WSL, which cannot read a Windows path
    res = subprocess.run(["bash", "-n", "real_host_prepare.sh"], cwd=str(TOOLS), capture_output=True, text=True)
    assert res.returncode == 0, res.stderr


@pytest.fixture
def stress():
    mod = load(FIXTURE, "stress_fixture")
    server = mod.Server(("127.0.0.1", 0), mod.Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield mod, server.server_address[1]
    server.shutdown()
    server.server_close()


def get(port: int, path: str):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    conn.request("GET", path)
    r = conn.getresponse()
    return r.status, json.loads(r.read() or b"{}")


def test_the_stress_fixture_answers_and_reports_what_it_gets(stress, tmp_path, monkeypatch):
    mod, port = stress
    monkeypatch.setenv("FLAG", "L3m0" + "n{" + "0" * 32 + "}")
    assert get(port, "/health") == (200, {"status": "ok"})
    st, body = get(port, "/flag")
    assert st == 200 and body["flag"].startswith("L3m0" + "n{")
    st, body = get(port, "/id")
    assert st == 200 and "caps" in body and "addresses" in body
    st, body = get(port, "/write?path=" + str(tmp_path / "x.txt").replace("\\", "/"))
    assert st == 200 and body["written"] is True
    st, body = get(port, "/write?path=" + str(tmp_path / "no-such-folder" / "x.txt").replace("\\", "/"))
    assert st == 200 and body["written"] is False and "error" in body
    st, body = get(port, "/cpu?s=0.3")
    assert st == 200 and body["wall_s"] >= 0.25 and body["cpu_s"] >= 0
    assert get(port, "/nope")[0] == 404


def test_the_stress_fixture_can_probe_the_network_from_inside(stress):
    mod, port = stress
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(5)
    lport = listener.getsockname()[1]

    def serve():
        c, _ = listener.accept()
        c.sendall(b"banner\n")
        c.close()

    threading.Thread(target=serve, daemon=True).start()
    st, body = get(port, f"/tcp?host=127.0.0.1&port={lport}&timeout=2")
    assert st == 200 and body["connected"] is True and "banner" in body["received"]
    free = socket.socket()
    free.bind(("127.0.0.1", 0))
    closed = free.getsockname()[1]
    free.close()
    st, body = get(port, f"/tcp?host=127.0.0.1&port={closed}&timeout=1")
    assert st == 200 and body["connected"] is False
    st, body = get(port, f"/scan?host=127.0.0.1&first={lport}&last={lport}&timeout=1")
    assert st == 200 and body["range"] == [lport, lport]
    listener.close()
