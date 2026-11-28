"""The router's route-polling loop must never be able to disappear silently.

Found on the real challenge host on 28 Nov 2026, 14:31 IST: the host router's route table froze, every new instance answered "Instance not found" (404), and the
only trace was one line, "asyncio: Task was destroyed but it is pending!". The cause: main() started the polling loop with a bare asyncio.create_task(...) and kept no
reference to the task. The event loop holds tasks only weakly, so Python's garbage collector may destroy a running task at any moment.

These tests pin the three defences: (1) background tasks are kept alive and a task that ends is reported, (2) a watchdog makes the router exit (Docker restarts it)
when polling has stalled, (3) closing a TCP listener cannot wait for ever on a relay that ignores its cancellation.
"""
import asyncio
import gc
import logging
import pathlib

import pytest

from services.instancer.router import background
from services.instancer.router.router import InstanceRouter

MAIN_SOURCE = pathlib.Path(background.__file__).with_name("main.py").read_text(encoding="utf-8")


def test_main_starts_its_polling_loops_through_the_helper_that_keeps_a_reference():
    """No bare create_task of a poll loop (a task nobody refers to can be garbage-collected while it runs)."""
    assert "asyncio.create_task(router.poll" not in MAIN_SOURCE
    assert "background.spawn(router.poll_routes_loop()" in MAIN_SOURCE
    assert "background.spawn(router.poll_central_routes_loop()" in MAIN_SOURCE


@pytest.mark.asyncio
async def test_a_spawned_task_is_referenced_while_it_runs_and_forgotten_when_done():
    started = asyncio.Event()
    release = asyncio.Event()

    async def work():
        started.set()
        await release.wait()

    task = background.spawn(work(), name="test-work")
    await started.wait()
    assert task in background.running_tasks()
    release.set()
    await task
    await asyncio.sleep(0)
    assert task not in background.running_tasks()


@pytest.mark.asyncio
async def test_a_spawned_loop_keeps_running_through_garbage_collection():
    ticks = {"n": 0}

    async def loop():
        while True:
            ticks["n"] += 1
            await asyncio.sleep(0.005)

    background.spawn(loop(), name="test-loop")  # the return value is dropped on purpose, as main() does
    gc.collect()
    gc.collect()
    gc.collect()
    before = ticks["n"]
    await asyncio.sleep(0.1)
    gc.collect()
    await asyncio.sleep(0.05)
    assert ticks["n"] > before + 3
    for t in list(background.running_tasks()):
        t.cancel()
    await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_a_background_task_that_ends_by_error_is_reported_loudly(caplog):
    async def boom():
        raise RuntimeError("the poller fell over")

    with caplog.at_level(logging.CRITICAL, logger="l3mon.instancer.router"):
        task = background.spawn(boom(), name="boom-loop")
        await asyncio.gather(task, return_exceptions=True)
        await asyncio.sleep(0)
    text = " ".join(r.getMessage() for r in caplog.records)
    assert "boom-loop" in text and "the poller fell over" in text


@pytest.mark.asyncio
async def test_the_watchdog_ends_the_process_when_polling_has_stalled():
    router = InstanceRouter(instance_domain="example.com", instancer_url="http://127.0.0.1:8300", router_token="t")
    router.last_poll_activity -= 1000  # nothing has happened for a long time
    exits = []
    task = asyncio.create_task(background.watch_polling(router, stall_seconds=5.0, check_every=0.01, exit_fn=exits.append))
    await asyncio.sleep(0.15)
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    assert exits == [1]


@pytest.mark.asyncio
async def test_the_watchdog_stays_quiet_while_polling_is_alive():
    router = InstanceRouter(instance_domain="example.com", instancer_url="http://127.0.0.1:8300", router_token="t")
    exits = []
    task = asyncio.create_task(background.watch_polling(router, stall_seconds=0.5, check_every=0.01, exit_fn=exits.append))
    for _ in range(20):
        router.note_poll_activity()
        await asyncio.sleep(0.02)
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    assert exits == []


@pytest.mark.asyncio
async def test_sync_tcp_routes_returns_even_when_a_relay_ignores_its_cancellation():
    """A relay stuck in its cleanup used to hold the whole poll loop for ever (gather without a limit)."""
    router = InstanceRouter(instance_domain="example.com", instancer_url="http://127.0.0.1:8300", router_token="t", tcp_port_start=31000, tcp_port_count=20)
    router.tcp_cancel_wait_seconds = 0.2

    class FakeServer:
        def close(self):
            pass

        async def wait_closed(self):
            return None

    async def stubborn():
        while True:
            try:
                await asyncio.sleep(3600)
            except asyncio.CancelledError:
                await asyncio.sleep(3600)  # swallows the cancellation and carries on

    task = asyncio.create_task(stubborn())
    await asyncio.sleep(0)
    router._tcp_listeners[31001] = FakeServer()
    router._tcp_connections[31001].add(task)
    try:
        await asyncio.wait_for(router.sync_tcp_routes([]), timeout=3.0)  # no route wants port 31001 any more: its listener must be closed
    finally:
        task.cancel()
    assert 31001 not in router._tcp_listeners


@pytest.mark.asyncio
async def test_every_poll_attempt_counts_as_activity_even_when_it_fails():
    """A down agent is not a stuck loop: a failing poll must not trigger the watchdog."""
    router = InstanceRouter(instance_domain="example.com", instancer_url="http://127.0.0.1:1", router_token="t", poll_interval=0.01)
    router.last_poll_activity -= 1000
    task = asyncio.create_task(router.poll_routes_loop())
    await asyncio.sleep(0.2)
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    assert router.seconds_since_poll_activity() < 5.0
