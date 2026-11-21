"""A challenge that misbehaves on purpose, to prove on a real host that the limits of an instance hold (tools/real_host_proof.py).

It is test equipment only: it is built and run on a challenge host by the proof script and never ships with a real challenge.
Every endpoint tries to exceed one limit and reports what it got:
  /id               who the process is (uid, capabilities, no-new-privileges, addresses)
  /mem?mb=N         touch N MB of memory (the memory limit must kill the container, not the host)
  /fork?n=N         start up to N processes (the process limit must stop it)
  /cpu?s=N          burn CPU for N seconds and report the CPU time it really got (the CPU quota must hold it near cpus x wall time)
  /disk?mb=N        write N MB into /tmp (a small tmpfs: the write must fail at its size, not fill the host's disk)
  /write?path=P     write a file at P (the root file system is read-only: it must fail)
  /tcp?host=H&port=P[&send=S]   connect from inside the instance, as a player with code execution would, and report what answers
  /scan?host=H&first=A&last=B   scan a port range from inside the instance
"""
import http.server
import json
import os
import socketserver
import subprocess
import threading
import time
import urllib.parse

PORT = int(os.environ.get("PORT", "8080"))


def _caps() -> dict:
    out = {}
    try:
        with open("/proc/self/status", encoding="ascii") as f:
            for line in f:
                key, _, value = line.partition(":")
                if key in ("CapEff", "CapPrm", "CapBnd", "NoNewPrivs", "Uid", "Gid"):
                    out[key] = value.strip()
    except OSError as e:
        out["error"] = str(e)
    return out


def _addresses() -> list:
    try:
        with open("/proc/net/fib_trie", encoding="ascii") as f:
            text = f.read()
    except OSError:
        return []
    found = []
    last = ""
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("|--"):
            last = line[3:].strip()
        elif line.startswith("/32 host LOCAL") and last and last not in found:
            found.append(last)
    return found


def do_mem(mb: int) -> dict:
    blocks = []
    for _ in range(mb):
        b = bytearray(1024 * 1024)
        for i in range(0, len(b), 4096):
            b[i] = 1
        blocks.append(b)
    return {"allocated_mb": len(blocks)}


def do_fork(n: int) -> dict:
    procs = []
    err = None
    for _ in range(n):
        try:
            procs.append(subprocess.Popen(["sleep", "20"]))
        except OSError as e:
            err = f"{type(e).__name__}: {e}"
            break
    started = len(procs)
    for p in procs:
        p.kill()
    for p in procs:
        p.wait()
    return {"requested": n, "started": started, "stopped_by": err}


def do_cpu(seconds: float) -> dict:
    def burn(stop_at: float):
        x = 0
        while time.monotonic() < stop_at:
            x += 1

    t0 = time.monotonic()
    c0 = time.process_time()
    th = threading.Thread(target=burn, args=(t0 + seconds,))
    th.start()
    th.join()
    return {"wall_s": round(time.monotonic() - t0, 2), "cpu_s": round(time.process_time() - c0, 2)}


def do_disk(mb: int) -> dict:
    written = 0
    err = None
    path = "/tmp/fill.bin"
    chunk = b"\0" * (1024 * 1024)
    try:
        with open(path, "wb") as f:
            for _ in range(mb):
                f.write(chunk)
                f.flush()
                os.fsync(f.fileno())
                written += 1
    except OSError as e:
        err = f"{type(e).__name__}: {e}"
    try:
        os.remove(path)
    except OSError:
        pass
    return {"requested_mb": mb, "written_mb": written, "stopped_by": err}


def do_tcp(host: str, port: int, send: str, timeout: float) -> dict:
    """What a player with code execution inside the instance can do on the network: connect, optionally send one line, read what comes back."""
    import socket

    try:
        s = socket.create_connection((host, port), timeout=timeout)
    except OSError as e:
        return {"host": host, "port": port, "connected": False, "error": f"{type(e).__name__}: {e}"}
    try:
        s.settimeout(timeout)
        if send:
            s.sendall(send.encode("utf-8") + b"\n")
        data = b""
        try:
            data = s.recv(300)
        except OSError:
            pass
        return {"host": host, "port": port, "connected": True, "received": data.decode("utf-8", errors="replace")}
    finally:
        s.close()


def do_scan(host: str, first: int, last: int, timeout: float) -> dict:
    import socket
    from concurrent.futures import ThreadPoolExecutor

    def probe(port: int):
        try:
            s = socket.create_connection((host, port), timeout=timeout)
            s.close()
            return port
        except OSError:
            return None

    with ThreadPoolExecutor(max_workers=50) as pool:
        open_ports = [p for p in pool.map(probe, range(first, last + 1)) if p is not None]
    return {"host": host, "range": [first, last], "open": open_ports}


def do_write(path: str) -> dict:
    try:
        with open(path, "w", encoding="ascii") as f:
            f.write("x")
        return {"path": path, "written": True}
    except OSError as e:
        return {"path": path, "written": False, "error": f"{type(e).__name__}: {e}"}


class Handler(http.server.BaseHTTPRequestHandler):
    def _json(self, status: int, data: dict) -> None:
        body = (json.dumps(data) + "\n").encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        q = dict(urllib.parse.parse_qsl(parsed.query))
        try:
            if parsed.path == "/health":
                return self._json(200, {"status": "ok"})
            if parsed.path == "/flag":
                return self._json(200, {"flag": os.environ.get("FLAG", "NO_FLAG_SET")})
            if parsed.path == "/id":
                return self._json(200, {"caps": _caps(), "addresses": _addresses(), "pid": os.getpid()})
            if parsed.path == "/mem":
                return self._json(200, do_mem(int(q.get("mb", "100"))))
            if parsed.path == "/fork":
                return self._json(200, do_fork(int(q.get("n", "500"))))
            if parsed.path == "/cpu":
                return self._json(200, do_cpu(float(q.get("s", "5"))))
            if parsed.path == "/disk":
                return self._json(200, do_disk(int(q.get("mb", "200"))))
            if parsed.path == "/write":
                return self._json(200, do_write(q.get("path", "/etc/hosts-test")))
            if parsed.path == "/tcp":
                return self._json(200, do_tcp(q["host"], int(q["port"]), q.get("send", ""), float(q.get("timeout", "1.5"))))
            if parsed.path == "/scan":
                return self._json(200, do_scan(q["host"], int(q["first"]), int(q["last"]), float(q.get("timeout", "0.3"))))
        except Exception as e:  # reported, never hidden
            return self._json(500, {"error": f"{type(e).__name__}: {e}"})
        return self._json(404, {"error": "not found"})

    def log_message(self, format, *args):
        pass


class Server(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    allow_reuse_address = True


if __name__ == "__main__":
    print(f"stress fixture on {PORT}", flush=True)
    Server(("", PORT), Handler).serve_forever()
