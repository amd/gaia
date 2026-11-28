# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Fire-and-forget cleanup tasks for streaming proxies."""

import asyncio
import logging
from typing import Coroutine

# asyncio only weak-refs pending tasks, so a spawned task needs a strong ref.
_background_tasks: "set[asyncio.Task]" = set()


def spawn_background(
    coro: Coroutine, *, log: logging.Logger, owner: str, task_kind: str = "cleanup"
) -> None:
    """Run *coro* on the running loop without letting it be garbage-collected.

    Args:
        coro: The cleanup coroutine.
        log: Logger the no-loop warning goes to, so it names the caller.
        owner: Who spawned it, the start of that warning (``"daemon relay"``).
        task_kind: What the task does, as named in that warning.
    """
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        # Loop already gone (server teardown): cleanup cannot run, so the
        # upstream response/client leak until GC — name it instead of hiding it.
        log.warning(
            f"{owner}: no running loop for background {task_kind} task; "
            "upstream connection will be reclaimed by GC"
        )
        coro.close()
        return
    task = loop.create_task(coro)
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)
