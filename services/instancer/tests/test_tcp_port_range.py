"""The TCP relay port range of a challenge host is configurable, from one pair of numbers that the agent and the router both read.

Found while sizing for 878 players in 446 teams (21 Nov 2026): the range was fixed at 30000-30199, so a host could never run more than 200 raw-TCP instances at
once, and with 446 teams and a few pwn challenges the demand at the peak can be several hundred. A team that finds the ports used up is refused, which is
exactly the unfairness the sizing is meant to rule out. The range is now INSTANCER_TCP_PORT_START .. INSTANCER_TCP_PORT_END (at most 2000 ports).
"""
import pytest

from services.instancer.main import read_capacity_limits  # noqa: F401  (the agent's own settings module must still import)
from services.instancer.ports import MAX_TCP_PORTS, read_tcp_port_range
from services.instancer.router.main import parse_router_config


def test_the_defaults_are_the_old_range():
    assert read_tcp_port_range({}) == (30000, 200)


def test_start_and_end_give_the_range():
    assert read_tcp_port_range({"INSTANCER_TCP_PORT_START": "30000", "INSTANCER_TCP_PORT_END": "30999"}) == (30000, 1000)
    assert read_tcp_port_range({"INSTANCER_TCP_PORT_END": "30499"}) == (30000, 500)


def test_start_and_count_still_work_for_existing_settings():
    assert read_tcp_port_range({"INSTANCER_TCP_PORT_START": "31000", "INSTANCER_TCP_PORT_COUNT": "50"}) == (31000, 50)


def test_a_count_and_an_end_that_disagree_are_an_error_not_a_guess():
    with pytest.raises(ValueError, match="INSTANCER_TCP_PORT"):
        read_tcp_port_range({"INSTANCER_TCP_PORT_START": "30000", "INSTANCER_TCP_PORT_COUNT": "200", "INSTANCER_TCP_PORT_END": "30999"})
    assert read_tcp_port_range({"INSTANCER_TCP_PORT_START": "30000", "INSTANCER_TCP_PORT_COUNT": "200", "INSTANCER_TCP_PORT_END": "30199"}) == (30000, 200)


@pytest.mark.parametrize("env", [
    {"INSTANCER_TCP_PORT_START": "30100", "INSTANCER_TCP_PORT_END": "30000"},   # end before start
    {"INSTANCER_TCP_PORT_START": "80"},                                          # a privileged port
    {"INSTANCER_TCP_PORT_END": "65536"},                                         # past the last port
    {"INSTANCER_TCP_PORT_START": "20000", "INSTANCER_TCP_PORT_END": "29999"},    # more ports than the limit
    {"INSTANCER_TCP_PORT_START": "x"},
    {"INSTANCER_TCP_PORT_COUNT": "0"},
])
def test_a_bad_range_is_refused(env):
    with pytest.raises(ValueError, match="INSTANCER_TCP_PORT"):
        read_tcp_port_range(env)


def test_the_limit_is_the_one_the_agent_and_router_already_enforce():
    assert MAX_TCP_PORTS == 2000


def test_the_router_reads_the_same_range():
    cfg = parse_router_config({"INSTANCER_TCP_PORT_START": "30000", "INSTANCER_TCP_PORT_END": "30999"})
    assert (cfg["tcp_port_start"], cfg["tcp_port_count"]) == (30000, 1000)
    default = parse_router_config({})
    assert (default["tcp_port_start"], default["tcp_port_count"]) == (30000, 200)
