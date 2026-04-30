# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Event-emitter Protocol for ``gaia.connections``.

The router (``src/gaia/ui/routers/connections.py``) implements this protocol
with a per-subscriber bounded ``asyncio.Queue`` and registers itself via
``set_emitter`` at app startup. Other callers (CLI / SDK) leave the emitter
unset; the no-op default emits to logging only.

This is a Protocol, not an ABC, because GAIA's mixin style is
duck-typed throughout. The router does not need to inherit anything.
"""

from __future__ import annotations

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
