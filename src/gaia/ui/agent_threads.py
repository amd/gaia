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
import logging
import threading
from typing import Any, Callable

logger = logging.getLogger(__name__)


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
