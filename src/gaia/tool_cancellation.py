# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Cooperative cancellation for tool calls the agent has stopped waiting for,
and the log-isolation filter built on top of it (#2600).

Kept dependency-free (stdlib only) on purpose: ``gaia.logger`` wires
``AbandonedWorkerLogFilter`` onto the root console/file handlers at import
time, and ``gaia.logger`` sits below ``gaia.agents`` in the import graph
(``gaia/agents/__init__.py`` imports ``get_logger`` from it) -- anything
here that pulled in ``gaia.agents.*`` would make that a cycle.
``gaia.agents.base.tools`` re-exports these names for existing callers.
"""

import logging
import threading
from typing import Any, Callable, Optional


class ToolCancelled(Exception):
    """Raised inside a tool body once the agent has abandoned the call."""

    def __init__(self, message: str = "tool call was cancelled after it timed out"):
        super().__init__(message)


# Per-worker cancellation flag, set by ``Agent._call_tool_bounded`` when a tool
# overruns its window. Python cannot kill a thread, so an abandoned worker runs
# to completion unless it opts in by checking this — and for a multi-minute tool
# that means a second job racing the first on the same hardware (#2600).
_cancellation = threading.local()


def set_tool_cancel_event(event: Optional[threading.Event]) -> None:
    """Bind *event* as the cancellation flag for the calling thread."""
    _cancellation.event = event


def current_cancel_event() -> Optional[threading.Event]:
    """The calling thread's cancellation flag, for a helper thread to poll.

    The flag is thread-local, so code that waits on other threads (a child
    process, a pipe reader) captures it here on the tool's own thread first.
    """
    return getattr(_cancellation, "event", None)


def tool_cancelled() -> bool:
    """True once the agent has stopped waiting for this tool.

    Long-running tools should poll this between stages and stop early. Anything
    that finishes well inside its timeout can ignore it.
    """
    event = getattr(_cancellation, "event", None)
    return event is not None and event.is_set()


def raise_if_cancelled() -> None:
    """Abort a tool body the agent has already given up on."""
    if tool_cancelled():
        raise ToolCancelled()


def run_cancellable(work: Callable[[], Any], poll_seconds: float = 0.25) -> Any:
    """Run a blocking call the tool can walk away from.

    A network request or native library call cannot check for cancellation, so
    Stop would otherwise wait minutes for it. The call runs on a daemon thread
    and is abandoned on cancellation: nothing reads its result, and it ends when
    the call does.

    Raises:
        ToolCancelled: The agent stopped waiting.
    """
    event = current_cancel_event()
    if event is None:
        return work()

    outcome: dict = {}

    def run() -> None:
        try:
            outcome["value"] = work()
        except BaseException as e:  # pylint: disable=broad-except
            outcome["error"] = e

    worker = threading.Thread(target=run, name="cancellable-call", daemon=True)
    worker.start()
    while worker.is_alive():
        worker.join(poll_seconds)
        if worker.is_alive() and event.is_set():
            raise ToolCancelled()
    if "error" in outcome:
        raise outcome["error"]
    return outcome["value"]


class AbandonedWorkerLogFilter(logging.Filter):
    """Drop log records emitted by a tool worker thread after its timeout.

    ``filter()`` always runs on the thread that made the log call (log
    records are created synchronously on the calling thread's stack), so
    ``tool_cancelled()`` — the same thread-local flag ``_call_tool_bounded``
    flips on timeout — correctly identifies an abandoned worker's own
    records here, regardless of which logger name it used.

    This only suppresses records reaching a handler this filter is attached
    to. GAIA's own root console/file handlers get it via ``GaiaLogger``
    (``logger.py``); a caller wiring up its own handler (e.g. a test's log
    capture, or an embedding application) must attach it there too for the
    guarantee to hold on that handler.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        return not tool_cancelled()
