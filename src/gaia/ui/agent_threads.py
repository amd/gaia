# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Run blocking agent work on its own thread, awaitable from the event loop.

Agent turns can block for minutes, and a stopped one can stay stuck in a tool
call that never looks at its cancel flag. On the event loop's default executor
such a thread holds a pool worker that the SSE stream loop and indexing also
need, so a few stuck turns starve every chat. A dedicated daemon thread costs
nothing to the pool and can be left to finish on its own.
"""

from __future__ import annotations

import asyncio
import threading
from typing import Any, Callable

from gaia.logger import get_logger

logger = get_logger(__name__)


def run_in_own_thread(fn: Callable[[], Any], *, name: str) -> asyncio.Future:
    """Start ``fn()`` on a new daemon thread; the future settles with its outcome.

    Waiting on the future with a timeout never stops the thread: callers that
    give up on it decide what the still-running thread may keep holding.
    """
    loop = asyncio.get_running_loop()
    future: asyncio.Future = loop.create_future()

    def settle(result: Any, error: BaseException | None) -> None:
        if future.cancelled():
            return
        if error is not None:
            future.set_exception(error)
        else:
            future.set_result(result)

    def target() -> None:
        try:
            result, error = fn(), None
        except BaseException as exc:  # handed to the awaiting coroutine
            result, error = None, exc
        try:
            loop.call_soon_threadsafe(settle, result, error)
        except RuntimeError:
            # The server shut down while this thread was still working.
            logger.warning(
                "Agent thread %s finished after the event loop closed; "
                "its result was dropped",
                name,
            )

    threading.Thread(target=target, name=name, daemon=True).start()
    return future


async def wait_until_off_the_model(
    worker: asyncio.Future, console, *, what: str, log_every: float = 60.0
) -> bool:
    """After telling *worker*'s agent to stop, wait while it may use the model.

    A model call in progress keeps the model (the agent ends it at the next
    token once its console is cancelled). Returns True once the thread has
    finished, or False as soon as it is inside a tool (*console*'s
    ``tool_running()``): outside any model call, and, told to stop, it starts
    no other, so the caller can free the model while the tool returns.
    """
    waited = 0.0
    while True:
        done, _ = await asyncio.wait({worker}, timeout=0.5)
        if done:
            return True
        if console is not None and console.tool_running():
            return False
        waited += 0.5
        if waited % log_every == 0:
            logger.warning(
                "%s still stopping after %.0f s; it keeps the model until its "
                "model call ends",
                what,
                waited,
            )
