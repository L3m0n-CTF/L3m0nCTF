"""The TCP relay port range of a challenge host: one pair of numbers that the agent and the router both read.

INSTANCER_TCP_PORT_START (default 30000) and INSTANCER_TCP_PORT_END (default 30199; at most 2000 ports in all). INSTANCER_TCP_PORT_COUNT is still read when
no END is given (the older way); when both are given they must agree. The compose file publishes the same range from TCP_PORT_FIRST and TCP_PORT_LAST, and the
cloud firewall must allow it for the event (docs/deploy/instancer.md, "Sizing a challenge host").
"""
import os

MAX_TCP_PORTS = 2000
DEFAULT_START = 30000
DEFAULT_COUNT = 200


def read_tcp_port_range(env=None) -> tuple[int, int]:
    """(first port, number of ports) from the environment, checked."""
    e = os.environ if env is None else env

    def number(name: str):
        raw = str(e.get(name, "")).strip()
        if not raw:
            return None
        try:
            return int(raw)
        except ValueError:
            raise ValueError(f"{name} must be a whole number, got {raw!r}") from None

    start = number("INSTANCER_TCP_PORT_START")
    end = number("INSTANCER_TCP_PORT_END")
    count = number("INSTANCER_TCP_PORT_COUNT")
    start = DEFAULT_START if start is None else start
    if end is not None:
        derived = end - start + 1
        if count is not None and count != derived:
            raise ValueError(f"INSTANCER_TCP_PORT_COUNT ({count}) and INSTANCER_TCP_PORT_END ({end}) disagree for a start of {start}: give one of them")
        count = derived
    elif count is None:
        count = DEFAULT_COUNT
    if not (1024 <= start <= 65535):
        raise ValueError(f"INSTANCER_TCP_PORT_START must be between 1024 and 65535, got {start}")
    if not (1 <= count <= MAX_TCP_PORTS):
        raise ValueError(f"INSTANCER_TCP_PORT range must hold between 1 and {MAX_TCP_PORTS} ports, got {count}")
    if start + count - 1 > 65535:
        raise ValueError(f"INSTANCER_TCP_PORT range [{start}, {start + count - 1}] exceeds 65535")
    return start, count
