# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Event-emitter Protocol for ``gaia.connectors``.

The router (``src/gaia/ui/routers/connections.py``) implements this protocol
with a per-subscriber bounded ``asyncio.Queue`` and registers itself via
``set_emitter`` at app startup. Other callers (CLI / SDK) leave the emitter
unset; the no-op default emits to logging only.

This is a Protocol, not an ABC, because GAIA's mixin style is
duck-typed throughout. The router does not need to inherit anything.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Optional, Protocol, runtime_checkable

logger = logging.getLogger(__name__)


@runtime_checkable
class EventEmitter(Protocol):
    """Async emit method used by ``flow.py``, ``store.py``, ``api.py``."""

    async def emit(self, event_type: str, payload: dict) -> None: ...


class _LoggingEmitter:
    """Default emitter when no caller has registered an active emitter
    (e.g. CLI / SDK contexts). Logs at INFO so events are visible in the
    user's terminal, but the Protocol contract is preserved."""

    async def emit(self, event_type: str, payload: dict) -> None:
        logger.info("connections-event %s: %s", event_type, payload)


_active_emitter: Optional[EventEmitter] = _LoggingEmitter()


def set_emitter(emitter: EventEmitter) -> None:
    """Register the active emitter. Idempotent — caller-side responsibility
    to re-set if the previous one is invalidated (e.g. on app restart)."""
    global _active_emitter
    _active_emitter = emitter


def reset_emitter() -> None:
    """Restore the no-op logging emitter (used by tests)."""
    global _active_emitter
    _active_emitter = _LoggingEmitter()


async def emit(event_type: str, payload: dict) -> None:
    """Emit an event through the currently-registered emitter."""
    if _active_emitter is not None:
        await _active_emitter.emit(event_type, payload)


def _log_emit_result(task: "asyncio.Task") -> None:
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        logger.error("connections-event emit task failed: %r", exc)


def emit_change(event_type: str, payload: dict) -> None:
    """Fire-and-forget synchronous wrapper around :func:`emit`.

    Lets synchronous callers (``api.py``, the CLI) trigger an event without
    being async themselves. Inside the UI server (a running event loop) the
    coroutine is scheduled on that loop and fanned out to SSE subscribers; in a
    bare process (the CLI) it is submitted to the persistent connector event
    loop (see ``_loop.py``) rather than ``asyncio.run``, avoiding Windows
    ProactorEventLoop teardown churn (#1579).
    Failures are logged, never silently swallowed.
    """
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if loop is not None:
        task = loop.create_task(emit(event_type, payload))
        task.add_done_callback(_log_emit_result)
    else:
        from gaia.connectors._loop import run_sync

        try:
            run_sync(emit(event_type, payload))
        except Exception:  # defensive: a notification must not break the write
            logger.exception("emit_change failed for %s", event_type)
