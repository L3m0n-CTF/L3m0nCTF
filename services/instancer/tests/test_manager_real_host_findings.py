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
