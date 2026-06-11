# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Persistent asyncio event-loop thread for the sync→async connector bridge.

Why this module exists
----------------------
Sync agent tool bodies (``Agent.process_query`` runs in a ThreadPoolExecutor
worker) need to call async token-refresh code.  The previous approach was
``asyncio.run(coro)`` — a new event loop per call.  That caused two coupled
defects:

- **Cross-loop Lock error (platform-independent):** an ``asyncio.Lock``
  created during one ``asyncio.run`` is bound to that loop.  A subsequent
  ``asyncio.run`` creates a *different* loop; attempting to use the Lock in
  it raises ``RuntimeError: <Lock> is bound to a different event loop``
  (Python ≤ 3.11; fixed in 3.12 but still a latent risk).

- **Windows ProactorEventLoop teardown hang:** repeated loop create/destroy
  cycles block in the ProactorEventLoop's internal teardown on Windows,
  causing the email-triage agent to hang indefinitely after ~21 token
  refreshes (#1579).

The fix: one lazily-initialised daemon thread runs a single asyncio event
loop for the lifetime of the process.  All connector async work submits to
that loop via ``asyncio.run_coroutine_threadsafe``.

Contextvar propagation
----------------------
``asyncio.run_coroutine_threadsafe`` does NOT inherit the caller's
contextvars — the coroutine starts with an empty context on the loop thread.
``run_sync`` explicitly captures ``contextvars.copy_context()`` at submit
time and runs the coroutine under it, preserving the agent-id contextvar set
by the agent runtime.

Deadlock guard
--------------
Calling ``.result()`` from the loop thread's own thread would block the loop
permanently.  ``run_sync`` raises ``RuntimeError`` if it detects it is being
called from the loop thread.
"""

from __future__ import annotations

import asyncio
import contextvars
import logging
import threading
import time
from typing import Any, Coroutine, TypeVar

from gaia.connectors.errors import ConnectorsError

logger = logging.getLogger(__name__)

_T = TypeVar("_T")

# Default timeout for run_sync calls (seconds).  30 s is generous for a
# token-endpoint round-trip (httpx timeout is 10 s inside _refresh_token)
# while still surfacing a hang within a reasonable debugging window.
_DEFAULT_TIMEOUT: float = 30.0

# Module-level singleton state — protected by _init_lock.
_loop: asyncio.AbstractEventLoop | None = None
_loop_thread: threading.Thread | None = None
_init_lock = threading.Lock()


def _get_persistent_loop() -> asyncio.AbstractEventLoop:
    """Return the singleton persistent event loop, starting it if necessary."""
    global _loop, _loop_thread

    # Fast path (no lock needed once initialised).
    if _loop is not None and _loop.is_running():
        return _loop

    with _init_lock:
        # Double-checked locking: re-test under the lock.
        if _loop is not None and _loop.is_running():
            return _loop

        new_loop = asyncio.new_event_loop()

        def _run_loop() -> None:
            asyncio.set_event_loop(new_loop)
            new_loop.run_forever()

        t = threading.Thread(target=_run_loop, name="gaia-connectors-loop", daemon=True)
        t.start()

        # Wait until the loop is actually running before returning it.
        deadline = 5.0
        start = time.monotonic()
        while not new_loop.is_running():
            if time.monotonic() - start > deadline:
                raise RuntimeError(
                    "gaia-connectors-loop thread did not start within "
                    f"{deadline}s. This is a bug — please report it."
                )
            time.sleep(0.001)

        _loop = new_loop
        _loop_thread = t
        logger.debug("gaia-connectors persistent loop started (thread=%s)", t.name)

    return _loop


async def _ctx_wrap(ctx: contextvars.Context, coro: Coroutine[Any, Any, _T]) -> _T:
    """Run *coro* under the captured caller context.

    Copies each contextvar from *ctx* into the running task's context so that
    async code on the loop thread sees the same agent-id (and any other
    contextvars) as the caller thread that submitted the work.
    """
    tokens = []
    for var, value in ctx.items():
        tokens.append(var.set(value))
    try:
        return await coro
    finally:
        for tok in tokens:
            tok.var.reset(tok)


def run_sync(
    coro: Coroutine[Any, Any, _T],
    *,
    timeout: float = _DEFAULT_TIMEOUT,
) -> _T:
    """Submit *coro* to the persistent loop and block until it completes.

    Parameters
    ----------
    coro:
        An awaitable coroutine created in the caller's thread.
    timeout:
        Maximum seconds to wait.  Raises ``ConnectorsError`` (actionable) if
        the coroutine does not complete within this window.

    Raises
    ------
    RuntimeError
        If called from the persistent loop's own thread (deadlock guard).
    ConnectorsError
        If the coroutine does not complete within *timeout* seconds.
    Any exception raised by *coro* propagates unchanged.
    """
    loop = _get_persistent_loop()

    # Deadlock guard: .result() from the loop thread blocks the loop.
    if threading.current_thread() is _loop_thread:
        raise RuntimeError(
            "run_sync() called from the gaia-connectors persistent loop thread. "
            "This would deadlock — call 'await <coro>' directly from async code "
            "running on this loop, or restructure to avoid re-entrancy."
        )

    # Capture the caller's contextvars so the coroutine on the loop thread
    # sees the same agent-id contextvar as the calling worker thread.
    ctx = contextvars.copy_context()

    future = asyncio.run_coroutine_threadsafe(_ctx_wrap(ctx, coro), loop)

    try:
        return future.result(timeout=timeout)
    except TimeoutError:
        future.cancel()
        raise ConnectorsError(
            f"Connector async operation timed out after {timeout}s. "
            "Check that the connector provider endpoint is reachable and "
            "that no background operation is blocking the connectors loop. "
            "See docs/sdk/infrastructure/connections.mdx."
        )
