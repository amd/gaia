# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Agent-identity context propagation for ``gaia.connectors``.

Three callables, asymmetric visibility:

- ``_agent_context(agent_id)`` — **PRIVATE**. Only the agent runtime calls
  this (via the private import path). A tool body cannot reach this from
  the public ``gaia.connectors`` API surface, so it cannot forge an agent
  identity to escalate scope (per plan amendment A9).

- ``current_agent_id()`` — **PUBLIC**. Tools and the connections core may
  read the active agent id but cannot set it.

- ``agent_runtime_active()`` — **PUBLIC**. True while an agent turn owns the
  current context. Credential access with no resolvable identity fails closed
  when this is True; outside an agent turn (CLI, SDK, debug) it stays the
  documented ungated escape hatch.

ContextVars are thread-local in CPython, but inherited across asyncio task
boundaries via ``contextvars.copy_context()``. This is exactly the model
the sync→async bridge relies on: ``Agent.process_query`` runs in a
``ThreadPoolExecutor`` worker, the context manager is entered there, and
``asyncio.run(get_access_token(...))`` from inside the worker inherits the
worker thread's context — see the bridge test in ``test_agent_bridge.py``.
The same rule is why any thread the runtime spawns to run a tool body must
be started with a copied context (``Agent._call_tool_bounded``); a bare
``threading.Thread`` starts empty and would drop the identity.
"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from typing import Iterator, Optional

_agent_id_var: ContextVar[str | None] = ContextVar(
    "gaia_connections_agent_id", default=None
)

_agent_runtime_var: ContextVar[bool] = ContextVar(
    "gaia_connections_agent_runtime", default=False
)


@contextmanager
def _agent_context(agent_id: Optional[str]) -> Iterator[None]:
    """
    Mark an agent turn — and bind its identity — for the ``with`` block.

    ``agent_id`` may be ``None`` for an agent with no namespaced identity
    (a directly-constructed test agent, an unregistered custom class). The
    runtime flag is set either way, so a credential request from inside that
    turn fails closed instead of silently taking the ungated path.

    PRIVATE — the agent runtime imports this via the explicit private path
    ``from gaia.connectors.context import _agent_context``. The connections
    public API (``gaia.connectors.__init__``) does NOT re-export this name,
    so a malicious tool body cannot forge an agent identity to bypass the
    per-agent grant check.
    """
    id_token = _agent_id_var.set(agent_id)
    runtime_token = _agent_runtime_var.set(True)
    try:
        yield
    finally:
        _agent_runtime_var.reset(runtime_token)
        _agent_id_var.reset(id_token)


def current_agent_id() -> str | None:
    """Return the active agent id, or ``None`` if no context is set."""
    return _agent_id_var.get()


def agent_runtime_active() -> bool:
    """Return True while an agent turn owns the current context.

    Read by ``handler.get_credential`` and ``api._authorize_access`` to decide
    whether a missing agent identity is a CLI caller (allowed) or a dropped
    context inside an agent turn (refused).
    """
    return _agent_runtime_var.get()
