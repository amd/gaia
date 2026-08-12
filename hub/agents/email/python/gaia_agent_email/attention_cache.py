# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Process-wide cache for the attention view (#2582).

Shared between the FastAPI route that serves it (``api_routes.py``) and the
answer-grounding guard that reconciles chat prose against it
(``answer_grounding.py``, #2636). Both consumers live in the same sidecar
process -- ``server.py`` mounts both routers on one FastAPI app -- and the
sidecar is single-tenant by design (``_SessionRegistry``'s own invariant in
``agent_routes.py``: "the sidecar hosts one user's agent"), so a bare
module-global is correct here, not a shortcut -- there is exactly one
attention view to cache, never one per session.

This module imports nothing from ``gaia_agent_email`` itself, so
``answer_grounding.py`` (deliberately dependency-light: no LLM calls, no I/O)
and ``api_routes.py`` (FastAPI-heavy) can both depend on it without either
depending on the other.
"""

from __future__ import annotations

import threading
import time
from typing import Any, Dict, Optional

# Seconds a cached attention view is treated as still describing what's on
# screen. One meaning, two consumers: api_routes.py's /attention route uses
# it to decide when to recompute; answer_grounding.py's contradiction guard
# uses the SAME window to decide when it is still safe to assume the cached
# items are still open (#2636) -- past it, the guard declines to correct
# rather than risk asserting a since-resolved item is still there.
ATTENTION_CACHE_TTL_SECONDS = 120.0

_cache: Optional[Dict[str, Any]] = None
_lock = threading.Lock()


def reset() -> None:
    """Clear the cache. Test-only seam."""
    global _cache
    with _lock:
        _cache = None


def store(record: Dict[str, Any], *, computed_at: Optional[float] = None) -> None:
    """Replace the cached record. ``computed_at`` defaults to now."""
    global _cache
    stamped = dict(record)
    stamped["_computed_at"] = time.time() if computed_at is None else computed_at
    with _lock:
        _cache = stamped


def peek() -> Optional[Dict[str, Any]]:
    """The raw cached record (including ``_computed_at``), or ``None`` when
    nothing has been computed yet this process.

    Never triggers a scan and never mutates the cache -- callers that need
    age-qualified output (e.g. ``cache_age_seconds``) build it themselves
    from ``_computed_at``.
    """
    with _lock:
        return _cache


__all__ = ["ATTENTION_CACHE_TTL_SECONDS", "peek", "reset", "store"]
