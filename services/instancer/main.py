import logging
import os
import re
import sys
import threading
import time

from services.instancer.docker_client import DockerClient
from services.instancer.lifetime import read_instance_lifetime
from services.instancer.manager import InstanceManager
from services.instancer.manifest import load_manifest
from services.instancer.ports import read_tcp_port_range
from services.instancer.server import create_instancer_server, create_metrics_server

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
_log = logging.getLogger("l3mon.instancer")


def _read_secret(name: str, default: str = "") -> str:
    path = f"/run/secrets/{name}"
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                val = f.read().strip()
                if val:
                    return val
        except Exception as e:
            _log.warning("Could not read secret file %s: %s", path, e)
    return os.getenv(name, default).strip()


def read_capacity_limits(env=None) -> tuple[int, int]:
    """(slots, memory in MB) this host admits: INSTANCER_MAX_SLOTS and INSTANCER_MAX_MEM_MB. Defaults 200 and 16384 (the old fixed figures).

    Set both from the machine's real size (docs/deploy/instancer.md, "Sizing a challenge host"): the memory figure is the sum of the declared limits of the
    instances that may run at once, and must leave room for the system, Docker and the router.
    """
    e = os.environ if env is None else env

    def one(name: str, default: int, low: int, high: int) -> int:
        raw = str(e.get(name, "")).strip()
        if not raw:
            return default
        try:
            value = int(raw)
        except ValueError:
            raise ValueError(f"{name} must be a whole number, got {raw!r}") from None
        if not (low <= value <= high):
            raise ValueError(f"{name} must be between {low} and {high}, got {value}")
        return value

    return one("INSTANCER_MAX_SLOTS", 200, 1, 5000), one("INSTANCER_MAX_MEM_MB", 16384, 64, 1_048_576)


def read_mem_overcommit(env=None) -> float:
    """INSTANCER_MEM_OVERCOMMIT (default 1.0, at most 8): a declared memory limit counts as limit / factor against INSTANCER_MAX_MEM_MB."""
    e = os.environ if env is None else env
    raw = str(e.get("INSTANCER_MEM_OVERCOMMIT", "")).strip()
    if not raw:
        return 1.0
    try:
        value = float(raw)
    except ValueError:
        raise ValueError(f"INSTANCER_MEM_OVERCOMMIT must be a number, got {raw!r}") from None
    if not (1.0 <= value <= 8.0):
        raise ValueError(f"INSTANCER_MEM_OVERCOMMIT must be between 1 and 8, got {value}")
    return value


def read_start_limits(env=None) -> tuple[int, float]:
    """(concurrent starts, seconds a start may wait for its turn): INSTANCER_MAX_CONCURRENT_STARTS (default 8) and INSTANCER_START_WAIT_SECONDS (default 35).

    The wait must stay below the platform's own timeout for a start (L3MON_INSTANCER_START_TIMEOUT, 45 s), so that the player is told "busy" by the agent
    rather than meeting a timeout of the platform.
    """
    e = os.environ if env is None else env

    def one(name: str, default, low, high, cast):
        raw = str(e.get(name, "")).strip()
        if not raw:
            return default
        try:
            value = cast(raw)
        except ValueError:
            raise ValueError(f"{name} must be a number, got {raw!r}") from None
        if not (low <= value <= high):
            raise ValueError(f"{name} must be between {low} and {high}, got {value}")
        return value

    return one("INSTANCER_MAX_CONCURRENT_STARTS", 8, 1, 64, int), one("INSTANCER_START_WAIT_SECONDS", 35, 1, 600, float)


def run_reaper_loop(manager: InstanceManager, interval: int = 5):
    """Periodically reap expired containers, then the ones that died."""
    while True:
        try:
            time.sleep(interval)
            reaped = manager.reap_expired()
            if reaped:
                _log.info("Reaped %d expired container(s): %s", len(reaped), reaped)
            dead = manager.reap_dead()
            if dead:
                _log.info("Tore down %d instance(s) whose container had died: %s", len(dead), dead)
        except Exception as e:
            _log.error("Error in reaper loop: %s", e)


def main():
    _log.info("Starting L3m0nCTF Challenge Instancer Service")
    dev_mode = os.getenv("INSTANCER_DEV", "0") in ("1", "true", "yes")
    agent_mode = os.getenv("INSTANCER_AGENT_MODE", "0") in ("1", "true", "yes")
    host_name = os.getenv("INSTANCER_HOST_NAME", "default").strip()

    clean_name = re.sub(r"[^A-Za-z0-9_]", "_", host_name.upper())
    instancer_token = _read_secret(f"INSTANCER_TOKEN_{clean_name}") or _read_secret("INSTANCER_TOKEN")
    router_token = _read_secret(f"ROUTER_TOKEN_{clean_name}") or _read_secret("ROUTER_TOKEN")
    if not instancer_token:
        _log.error("INSTANCER_TOKEN is not set or empty. Exiting.")
        sys.exit(1)

    manifest_path = os.getenv("MANIFEST_PATH", "/config/challenges.toml")
    if not os.path.exists(manifest_path):
        manifest_path = "config/instancer/challenges.example.toml"

    _log.info("Loading manifest from %s (dev_mode=%s, agent_mode=%s, host_name=%s)", manifest_path, dev_mode, agent_mode, host_name)
    manifest = load_manifest(manifest_path, dev_mode=dev_mode)

    docker = DockerClient()
    instance_domain = os.getenv("INSTANCE_DOMAIN", "localhost:8090")
    tcp_host = os.getenv("INSTANCER_TCP_HOST", "localhost")
    tcp_port_start, tcp_port_count = read_tcp_port_range()
    router_container_name = os.getenv("ROUTER_CONTAINER_NAME", "l3mon-router-1")
    max_slots, max_mem_mb = read_capacity_limits()
    _log.info("Admission ceilings: %d instances, %d MB of declared memory", max_slots, max_mem_mb)

    max_concurrent_starts, start_wait_seconds = read_start_limits()
    max_ttl_minutes, max_renewals = read_instance_lifetime()
    _log.info("Instance lifetime: at most %d minutes, %d renewal(s) allowed", max_ttl_minutes, max_renewals)
    manager = InstanceManager(
        max_ttl_minutes=max_ttl_minutes,
        max_renewals=max_renewals,
        mem_overcommit=read_mem_overcommit(),
        max_concurrent_starts=max_concurrent_starts,
        start_wait_seconds=start_wait_seconds,
        max_slots=max_slots,
        max_mem_mb=max_mem_mb,
        manifest=manifest,
        docker_client=docker,
        instance_domain=instance_domain,
        url_scheme=os.getenv("INSTANCE_URL_SCHEME", ""),  # empty = https for a real domain, http for a development address
        tcp_host=tcp_host,
        tcp_port_start=tcp_port_start,
        tcp_port_count=tcp_port_count,
        router_container_name=router_container_name,
        dev_mode=dev_mode,
        host_name=host_name,
        agent_mode=agent_mode,
    )

    # Reconcile existing containers from Docker labels
    _log.info("Reconciling active containers from Docker labels...")
    try:
        manager.reconcile_from_docker()
        active = manager.list_instances()
        _log.info("Discovered %d active instance(s) from Docker labels", len(active))
    except Exception as e:
        _log.warning("Failed to reconcile from Docker: %s", e)

    # Start TTL reaper thread
    reaper_thread = threading.Thread(target=run_reaper_loop, args=(manager,), daemon=True)
    reaper_thread.start()

    # Start metrics server on 8301
    m_server = create_metrics_server("0.0.0.0", 8301, manager)
    m_thread = threading.Thread(target=m_server.serve_forever, daemon=True)
    m_thread.start()
    _log.info("Metrics listener active on 0.0.0.0:8301")

    # Start main API server on 8300
    server = create_instancer_server("0.0.0.0", 8300, manager, instancer_token, router_token)
    _log.info("Instancer API active on 0.0.0.0:8300")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        _log.info("Shutting down instancer service...")
        server.shutdown()
        m_server.shutdown()


if __name__ == "__main__":
    main()
