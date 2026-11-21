"""What the real challenge host showed on 21 Nov 2026 (services/instancer/tools/real_host_proof.py), as unit tests with the fake Docker client.

1. The agent knew only a slot count (200 by default) and a memory figure of 16384 MB that it never enforced and that nobody could set: on an 8 GB machine
   it would have admitted instances well past the machine's memory. Admission now also stops at a memory ceiling, and both ceilings come from the environment.
2. When an instance's container died (a crash, or the memory limit killing it) the agent kept saying "running" until the TTL: the player saw a running
   instance that answered nothing, and the only way out was to stop it and wait for the 60 s cooldown. A liveness sweep now tears such an instance down and
   lets the team start again after a short wait.
3. The routes carry the instance's start time, so the router can give a young instance a moment to open its port.
"""
import pytest

from services.instancer.main import read_capacity_limits
from services.instancer.manager import CRASH_COOLDOWN_SECONDS, CapacityError, CooldownError, InstanceManager
from services.instancer.manifest import load_manifest_from_string

from test_instancer_core import FLAG_EXAMPLE, MANIFEST_TOML, MockDockerClient


def manager(max_mem_mb: int = 16384, max_slots: int = 200, t=None):
    clock = t if t is not None else [1700000000]
    docker = MockDockerClient()
    mgr = InstanceManager(
        manifest=load_manifest_from_string(MANIFEST_TOML, dev_mode=False),
        docker_client=docker,
        instance_domain="inst.example.com",
        max_mem_mb=max_mem_mb,
        max_slots=max_slots,
        time_fn=lambda: clock[0],
    )
    return mgr, docker, clock


def test_admission_stops_at_the_memory_ceiling_not_only_at_the_slot_count():
    mgr, docker, _ = manager(max_mem_mb=600)  # the sample challenges ask for 256 MB each: two fit, a third does not
    mgr.start_instance(team_id=1, challenge_id=1, slug="demo-web", flag=FLAG_EXAMPLE)
    mgr.start_instance(team_id=2, challenge_id=1, slug="demo-web", flag=FLAG_EXAMPLE)
    with pytest.raises(CapacityError, match="memory"):
        mgr.start_instance(team_id=3, challenge_id=1, slug="demo-web", flag=FLAG_EXAMPLE)
    assert len(docker.containers) == 2, "a refused start must leave no container behind"
    cap = mgr.get_capacity()
    assert cap["mem_mb_used"] == 512 and cap["mem_mb_free"] == 88


def test_a_stopped_instance_gives_its_memory_back():
    mgr, docker, _ = manager(max_mem_mb=600)
    a, _ = mgr.start_instance(team_id=1, challenge_id=1, slug="demo-web", flag=FLAG_EXAMPLE)
    mgr.start_instance(team_id=2, challenge_id=1, slug="demo-web", flag=FLAG_EXAMPLE)
    mgr.stop_instance(a["id"])
    mgr.start_instance(team_id=3, challenge_id=1, slug="demo-web", flag=FLAG_EXAMPLE)
    assert len(docker.containers) == 2


def test_the_ceilings_come_from_the_environment_with_the_old_defaults():
    assert read_capacity_limits({}) == (200, 16384)
    assert read_capacity_limits({"INSTANCER_MAX_SLOTS": "120", "INSTANCER_MAX_MEM_MB": "6000"}) == (120, 6000)
    for bad in ({"INSTANCER_MAX_SLOTS": "0"}, {"INSTANCER_MAX_SLOTS": "x"}, {"INSTANCER_MAX_MEM_MB": "-5"}, {"INSTANCER_MAX_MEM_MB": "10"}):
        with pytest.raises(ValueError, match="INSTANCER_MAX"):
            read_capacity_limits(bad)


def test_routes_carry_the_start_time():
    mgr, _, clock = manager()
    inst, _ = mgr.start_instance(team_id=1, challenge_id=1, slug="demo-web", flag=FLAG_EXAMPLE)
    assert [r["created_at"] for r in mgr.get_routes()] == [clock[0]] and inst["created_at"] == clock[0]


def test_a_dead_container_is_torn_down_and_the_team_may_start_again_after_a_short_wait():
    mgr, docker, clock = manager()
    inst, _ = mgr.start_instance(team_id=1, challenge_id=1, slug="demo-web", flag=FLAG_EXAMPLE)
    docker.containers[inst["id"]]["state"] = "exited"  # killed for memory, or crashed
    assert mgr.reap_dead() == [inst["id"]]
    assert inst["id"] not in docker.containers and f"l3mon_inst_{inst['id']}" not in docker.networks
    assert mgr.list_instances(team_id=1) == [], "the player sees no instance, so the page offers Start again"
    with pytest.raises(CooldownError) as e:
        mgr.start_instance(team_id=1, challenge_id=1, slug="demo-web", flag=FLAG_EXAMPLE)
    assert e.value.retry_after <= CRASH_COOLDOWN_SECONDS < 60, "a crash is not punished with the full 60 s stop cooldown"
    clock[0] += CRASH_COOLDOWN_SECONDS + 1
    again, created = mgr.start_instance(team_id=1, challenge_id=1, slug="demo-web", flag=FLAG_EXAMPLE)
    assert created and again["id"] != inst["id"]


def test_a_container_that_is_gone_altogether_counts_as_dead_too():
    mgr, docker, _ = manager()
    inst, _ = mgr.start_instance(team_id=1, challenge_id=1, slug="demo-web", flag=FLAG_EXAMPLE)
    docker.containers.pop(inst["id"])  # removed by hand
    assert mgr.reap_dead() == [inst["id"]]
    assert mgr.list_instances() == []


def test_the_liveness_sweep_leaves_running_instances_alone():
    mgr, docker, _ = manager()
    a, _ = mgr.start_instance(team_id=1, challenge_id=1, slug="demo-web", flag=FLAG_EXAMPLE)
    b, _ = mgr.start_instance(team_id=2, challenge_id=1, slug="demo-web", flag=FLAG_EXAMPLE)
    docker.containers[b["id"]]["state"] = "exited"
    assert mgr.reap_dead() == [b["id"]]
    assert [i["id"] for i in mgr.list_instances()] == [a["id"]] and a["id"] in docker.containers


def test_when_docker_cannot_be_asked_the_sweep_changes_nothing():
    mgr, docker, _ = manager()
    inst, _ = mgr.start_instance(team_id=1, challenge_id=1, slug="demo-web", flag=FLAG_EXAMPLE)

    def broken(label_filter=None):
        raise OSError("docker is not answering")

    docker.list_containers = broken
    assert mgr.reap_dead() == []
    assert [i["id"] for i in mgr.list_instances()] == [inst["id"]]


# ---------------------------------------------------------------------------------------------------------------------------------------------------
# A burst of starts (real host, 21 Nov 2026: 80 teams starting at the same instant)
#   - 13 of 80 requests were reset by the agent: its HTTP server kept Python's default listen backlog of 5;
#   - the 80 starts took 27.7 s in all (about 2.9 a second), the median start 16 s, and 55 of 80 were slower than the platform's 10 s timeout.
# The queue is deep now, and the work that Docker does for starts is limited to a few at a time, so that a burst makes the last start wait its turn
# instead of piling hundreds of Docker calls on the daemon at once; a start that has waited too long is told "busy, retry in a few seconds".
# ---------------------------------------------------------------------------------------------------------------------------------------------------
import threading
import time as _time

from services.instancer.server import ThreadedHTTPServer


def test_the_agents_http_server_has_a_deep_listen_queue():
    assert ThreadedHTTPServer.request_queue_size >= 256, "Python's default of 5 reset 13 of 80 simultaneous requests on the real host"


class SlowDocker(MockDockerClient):
    """A Docker that takes a while for each call and records how many calls run at the same moment."""

    def __init__(self, delay=0.05):
        super().__init__()
        self.delay, self.running, self.peak = delay, 0, 0
        self._lock = threading.Lock()

    def _slow(self):
        with self._lock:
            self.running += 1
            self.peak = max(self.peak, self.running)
        _time.sleep(self.delay)
        with self._lock:
            self.running -= 1

    def create_network(self, name, internal=True, labels=None):
        self._slow()
        return super().create_network(name, internal, labels)

    def start_container(self, container_id):
        self._slow()
        return super().start_container(container_id)


def slow_manager(limit, wait, delay=0.05):
    docker = SlowDocker(delay)
    mgr = InstanceManager(manifest=load_manifest_from_string(MANIFEST_TOML, dev_mode=False), docker_client=docker, instance_domain="inst.example.com",
                          max_concurrent_starts=limit, start_wait_seconds=wait)
    return mgr, docker


def test_only_a_few_starts_work_on_docker_at_the_same_moment():
    mgr, docker = slow_manager(limit=3, wait=30)
    results = []

    def go(i):
        results.append(mgr.start_instance(team_id=i + 1, challenge_id=1, slug="demo-web", flag=FLAG_EXAMPLE)[1])

    threads = [threading.Thread(target=go, args=(i,)) for i in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(results) == 20 and all(results), "every start in the burst must succeed, each in its turn"
    assert docker.peak <= 3, f"{docker.peak} Docker calls ran at once; the limit is 3"
    assert len(docker.containers) == 20


def test_a_start_that_waited_too_long_is_told_busy_and_leaves_nothing_behind():
    mgr, docker = slow_manager(limit=1, wait=0.2, delay=0.6)  # one at a time, each takes 0.6 s: the second waits more than the 0.2 s it may
    outcome = {}

    def first():
        outcome["first"] = mgr.start_instance(team_id=1, challenge_id=1, slug="demo-web", flag=FLAG_EXAMPLE)

    t = threading.Thread(target=first)
    t.start()
    _time.sleep(0.15)  # the first is on Docker now
    with pytest.raises(CapacityError, match="busy") as e:
        mgr.start_instance(team_id=2, challenge_id=1, slug="demo-web", flag=FLAG_EXAMPLE)
    assert e.value.retry_after >= 1
    t.join()
    assert "first" in outcome
    assert [i["team_id"] for i in mgr.list_instances()] == [1], "the refused start must not leave a record, a network, or a TCP port behind"
    assert len(docker.containers) == 1 and len(docker.networks) == 1
    # and the team that was told "busy" can start as soon as there is room (no cooldown was set)
    again, created = mgr.start_instance(team_id=2, challenge_id=1, slug="demo-web", flag=FLAG_EXAMPLE)
    assert created


def test_the_start_limit_comes_from_the_environment():
    from services.instancer.main import read_start_limits

    assert read_start_limits({}) == (8, 35)
    assert read_start_limits({"INSTANCER_MAX_CONCURRENT_STARTS": "4", "INSTANCER_START_WAIT_SECONDS": "10"}) == (4, 10)
    for bad in ({"INSTANCER_MAX_CONCURRENT_STARTS": "0"}, {"INSTANCER_MAX_CONCURRENT_STARTS": "x"}, {"INSTANCER_START_WAIT_SECONDS": "-1"}):
        with pytest.raises(ValueError, match="INSTANCER_"):
            read_start_limits(bad)


def test_after_a_host_reboot_the_instances_whose_containers_stopped_are_cleaned_up_not_kept_for_ever():
    """Real host, 21 Nov 2026: after a reboot the instance containers (no restart policy, on purpose) were stopped, the agent rebuilt its records from Docker's
    labels as state "error" and never touched them again: three dead records, three stopped containers and three networks stayed until somebody killed them
    by hand, and the agent counted 0 slots used while it listed 3 instances. The liveness sweep now covers these records too."""
    mgr, docker, clock = manager()
    inst, _ = mgr.start_instance(team_id=1, challenge_id=1, slug="demo-web", flag=FLAG_EXAMPLE)
    docker.containers[inst["id"]]["state"] = "exited"  # what a reboot does to a container with no restart policy
    fresh = InstanceManager(manifest=load_manifest_from_string(MANIFEST_TOML, dev_mode=False), docker_client=docker, instance_domain="inst.example.com", time_fn=lambda: clock[0])
    fresh.reconcile_from_docker()  # the agent starts again after the reboot
    assert [i["state"] for i in fresh.list_instances()] == ["error"]
    assert fresh.reap_dead() == [inst["id"]]
    assert inst["id"] not in docker.containers and f"l3mon_inst_{inst['id']}" not in docker.networks
    assert fresh.list_instances() == []
