"""Background tasks of the router that must never disappear without a trace.

Why this exists (found on the real challenge host, 28 Nov 2026, 14:31 IST): main() started the route-polling loop with a bare `asyncio.create_task(...)` and kept
no reference to the task. The event loop holds running tasks only weakly, so Python's garbage collector may destroy one in the middle of its work ("Task was
destroyed but it is pending!"). The router then went on serving with a route table frozen at that moment: every NEW instance answered "Instance not found"
while the container looked perfectly healthy. Three defences:

1. spawn() keeps a strong reference to every background task until it is done, and reports (at CRITICAL) a task that ends for any reason but cancellation.
2. watch_polling() ends the process when the polling loop has shown no sign of life for a long time; the container's restart policy brings the router back.
   "Sign of life" is an ATTEMPT, not a success: an agent that is down for a while must not make the router restart in a loop.
3. router.py bounds what the polling loop may wait for (see InstanceRouter.tcp_cancel_wait_seconds).
"""
import asyncio
import logging
import os
from typing import Awaitable, Callable, Optional

_log = logging.getLogger("l3mon.instancer.router")

_TASKS: set = set()

DEFAULT_STALL_SECONDS = 90.0
DEFAULT_CHECK_EVERY_SECONDS = 10.0


def running_tasks() -> set:
    """The background tasks that are alive (a copy)."""
    return set(_TASKS)


def spawn(coro: Awaitable, name: str) -> "asyncio.Task":
    """Start `coro` as a task that the router keeps referenced until it ends. Always use this, never a bare create_task, for anything that must keep running."""
    task = asyncio.get_running_loop().create_task(coro, name=name)
    _TASKS.add(task)

    def _finished(t: "asyncio.Task") -> None:
        _TASKS.discard(t)
        if t.cancelled():
            return
        exc = t.exception()
        _log.critical("Background task %s has ended%s: the router can no longer do what it is for", name, f" with {exc!r}" if exc is not None else "")

    task.add_done_callback(_finished)
    return task


async def watch_polling(
    router,
    stall_seconds: Optional[float] = None,
    check_every: Optional[float] = None,
    exit_fn: Callable[[int], None] = os._exit,
) -> None:
    """End the process (exit code 1) when `router` has made no poll attempt for `stall_seconds`. Docker restarts the container (restart: unless-stopped)."""
    if stall_seconds is None:
        stall_seconds = float(os.environ.get("ROUTER_POLL_STALL_SECONDS", DEFAULT_STALL_SECONDS))
    if check_every is None:
        check_every = min(DEFAULT_CHECK_EVERY_SECONDS, max(stall_seconds / 3.0, 0.01))
    while True:
        await asyncio.sleep(check_every)
        quiet = router.seconds_since_poll_activity()
        if quiet >= stall_seconds:
            _log.critical("The route-polling loop has shown no sign of life for %.0f s: the route table is frozen. Ending the process so that Docker restarts the router.", quiet)
            exit_fn(1)
            return
