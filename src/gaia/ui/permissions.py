# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Per-chat permission state for the Agent UI: the permission mode and grants.

A fresh ``SSEOutputHandler`` is built for every turn, so whatever the user
decided about permissions has to live somewhere that outlives a turn. This is
the Agent UI's counterpart of ``PermissionState`` in the flagship's stdio host
(``hub/agents/gaia/python/gaia_agent/stdio.py``), with the same semantics:

- **ask** (the default): every confirmation-gated tool call asks.
- **full access**: gated calls run without asking and the shell guardrails are
  lifted, exactly as the TUI's ``/full-access``. It starts on for a new chat only
  when ``full_access`` is set in ``~/.gaia/config.json``
  (``gaia config set full_access true`` or the TUI's ``/full-access always``).
- **grants**: "always allow" answers, scoped to the invocation by
  :func:`gaia.agents.base.tool_grants.grant_scope`. They last until the chat is
  deleted or the backend restarts, and can be listed and revoked.

State is in memory only on purpose: consent given in one run of GAIA must not
silently approve tools in the next.
"""

from __future__ import annotations

import threading
from typing import Any, Dict, List, Optional

from gaia.agents.base.tool_grants import grant_scope
from gaia.logger import get_logger

logger = get_logger(__name__)

MODE_ASK = "ask"
MODE_FULL_ACCESS = "full_access"
MODES = (MODE_ASK, MODE_FULL_ACCESS)

#: How long a permission prompt waits for the person, as in the TUI. Expiry denies.
CONFIRM_TIMEOUT_SECONDS = 600


def _default_full_access() -> bool:
    from gaia.config import GaiaConfig

    return bool(GaiaConfig.load().full_access)


class SessionPermissions:
    """The permission mode and "always allow" grants of one chat."""

    def __init__(self, full_access: bool = False) -> None:
        self._lock = threading.Lock()
        self._full_access = full_access
        self._grants: Dict[str, str] = {}
        self._handler: Any = None

    @property
    def mode(self) -> str:
        with self._lock:
            return MODE_FULL_ACCESS if self._full_access else MODE_ASK

    def set_mode(self, mode: str) -> None:
        if mode not in MODES:
            raise ValueError(
                f"Unknown permission mode {mode!r}. Known: {', '.join(MODES)}."
            )
        enabled = mode == MODE_FULL_ACCESS
        with self._lock:
            self._full_access = enabled
            if self._handler is not None:
                self._apply(self._handler)
        logger.warning("Agent UI full access %s", "ENABLED" if enabled else "disabled")

    def grants(self) -> List[Dict[str, str]]:
        with self._lock:
            return [{"key": k, "label": v} for k, v in self._grants.items()]

    def grant(self, tool_name: str, tool_args: Any) -> Optional[str]:
        """Record an "always" answer for this call; None when it has no scope."""
        scope = grant_scope(tool_name, tool_args)
        if scope is None:
            return None
        with self._lock:
            self._grants[scope.key] = scope.label
        return scope.label

    def revoke(self, key: Optional[str] = None) -> int:
        """Revoke one grant, or every grant when *key* is None. Returns the count."""
        with self._lock:
            keys = list(self._grants) if key is None else [key]
            removed = 0
            for k in keys:
                if self._grants.pop(k, None) is not None:
                    removed += 1
                if self._handler is not None:
                    self._handler.session_grants().discard(k)
            return removed

    def attach(self, handler: Any) -> None:
        """Give a turn's handler this chat's mode and grants."""
        with self._lock:
            self._apply(handler)
            handler.session_grants().update(self._grants)
            handler.confirm_timeout_seconds = CONFIRM_TIMEOUT_SECONDS
            self._handler = handler

    def detach(self, handler: Any) -> None:
        with self._lock:
            if self._handler is handler:
                self._handler = None

    def _apply(self, handler: Any) -> None:
        handler.auto_approve_gated_tools = self._full_access
        handler.full_access = self._full_access


_registry: Dict[str, SessionPermissions] = {}
_registry_lock = threading.Lock()


def for_session(session_id: str) -> SessionPermissions:
    """The permission state of *session_id*, created on first use."""
    with _registry_lock:
        perms = _registry.get(session_id)
        if perms is None:
            perms = SessionPermissions(full_access=_default_full_access())
            _registry[session_id] = perms
        return perms


def all_sessions() -> Dict[str, SessionPermissions]:
    """Every chat that has permission state in this backend run."""
    with _registry_lock:
        return dict(_registry)


def forget_session(session_id: str) -> None:
    """Drop a deleted chat's permissions so its grants cannot outlive it."""
    with _registry_lock:
        _registry.pop(session_id, None)


def reset_all() -> None:
    """Clear every chat's permission state. Tests only."""
    with _registry_lock:
        _registry.clear()
