# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Agent-identity context propagation for ``gaia.connectors``.

Two callables, asymmetric visibility:

- ``_agent_context(agent_id)`` — **PRIVATE**. Only the agent runtime calls
  this (via the private import path). A tool body cannot reach this from
  the public ``gaia.connectors`` API surface, so it cannot forge an agent
  identity to escalate scope (per plan amendment A9).

- ``current_agent_id()`` — **PUBLIC**. Tools and the connections core may
  read the active agent id but cannot set it.

ContextVars are thread-local in CPython, but inherited across asyncio task
boundaries via ``contextvars.copy_context()``. This is exactly the model
the sync→async bridge relies on: ``Agent.process_query`` runs in a
``ThreadPoolExecutor`` worker, the context manager is entered there, and
``asyncio.run(get_access_token(...))`` from inside the worker inherits the
worker thread's context — see the bridge test in ``test_agent_bridge.py``.
"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from typing import Iterator

_agent_id_var: ContextVar[str | None] = ContextVar(
    "gaia_connections_agent_id", default=None
)


@contextmanager
def _agent_context(agent_id: str) -> Iterator[None]:
    """
    Set the active agent id for the lifetime of the ``with`` block.

    PRIVATE — the agent runtime imports this via the explicit private path
    ``from gaia.connectors.context import _agent_context``. The connections
    public API (``gaia.connectors.__init__``) does NOT re-export this name,
    so a malicious tool body cannot forge an agent identity to bypass the
    per-agent grant check.
    """
    token = _agent_id_var.set(agent_id)
    try:
        yield
    finally:
        _agent_id_var.reset(token)


def current_agent_id() -> str | None:
    """Return the active agent id, or ``None`` if no context is set."""
    return _agent_id_var.get()
