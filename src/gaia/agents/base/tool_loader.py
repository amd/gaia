# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
ToolLoader — bundle-based tool visibility for agents.

Gates which tools appear in the LLM prompt each turn without changing the
global ``_TOOL_REGISTRY``.  The registry remains the source of truth for
*all* registered tools; the loader picks the subset that goes into the
system prompt.

Bundles
-------
A ``ToolBundle`` groups related tools under a name with an activation
policy.  Three policies exist:

* **always** — included in every prompt (e.g. ``core``).
* **session** — stays active for the rest of the session once any tool
  in the bundle has been used (e.g. ``scratchpad`` after ``create_table``).
* **keyword** — activated when the current user message matches one of a
  set of trigger patterns (e.g. ``browser`` on URL patterns).

The loader evaluates bundles in priority order each turn and returns the
set of tool names that should appear in the prompt.
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass
from enum import Enum
from typing import Dict, FrozenSet, List, Optional, Set

logger = logging.getLogger(__name__)


class ActivationPolicy(Enum):
    """How a bundle decides whether to be active."""

    ALWAYS = "always"
    SESSION = "session"  # Active once any tool in the bundle was used this session
    KEYWORD = "keyword"  # Active when user message matches trigger patterns


@dataclass(frozen=True)
class ToolBundle:
    """An immutable group of tools sharing an activation policy.

    Parameters
    ----------
    name:
        Human-readable bundle identifier (e.g. ``"rag"``, ``"scratchpad"``).
    tools:
        Frozenset of tool names that belong to this bundle.
    policy:
        When the bundle should be included in the prompt.
    keywords:
        Regex patterns (case-insensitive) checked against the user message
        when ``policy`` is ``KEYWORD``.  Ignored for other policies.
    """

    name: str
    tools: FrozenSet[str]
    policy: ActivationPolicy
    keywords: FrozenSet[str] = frozenset()


@dataclass
class _BundleState:
    """Mutable per-session state for a single bundle."""

    activated: bool = False  # True once the bundle has been activated this session
    last_used_ts: float = 0.0  # Timestamp of most recent tool use in this bundle


class ToolLoader:
    """Selects which registered tools appear in the LLM prompt each turn.

    Usage::

        loader = ToolLoader()
        loader.register_bundle(ToolBundle(
            name="scratchpad",
            tools=frozenset({"create_table", "insert_data", "query_data",
                             "list_tables", "drop_table"}),
            policy=ActivationPolicy.SESSION,
        ))

        # Each turn, ask which tools should be visible:
        active_tools = loader.resolve(user_message, registry)

    The loader does **not** modify ``_TOOL_REGISTRY``.  It returns a
    filtered view that the agent uses when building the prompt.
    """

    # Warm-window: if a bundle was used in the last 24 h, keep it active
    WARM_WINDOW_SECS: float = 24 * 3600

    def __init__(self) -> None:
        self._bundles: Dict[str, ToolBundle] = {}
        self._state: Dict[str, _BundleState] = {}
        # History of (tool_name, timestamp) for logging / warm-window checks
        self._tool_history: List[tuple[str, float]] = []
        # Reverse index: tool_name → bundle_name for fast lookup
        self._tool_to_bundle: Dict[str, str] = {}

    # ── registration ─────────────────────────────────────────────────────

    def register_bundle(self, bundle: ToolBundle) -> None:
        """Register a bundle (idempotent — overwrites if name already exists)."""
        self._bundles[bundle.name] = bundle
        self._state.setdefault(bundle.name, _BundleState())
        for tool_name in bundle.tools:
            self._tool_to_bundle[tool_name] = bundle.name

    def register_bundles(self, bundles: list[ToolBundle]) -> None:
        for b in bundles:
            self.register_bundle(b)

    # ── per-turn resolution ──────────────────────────────────────────────

    def resolve(
        self,
        user_message: str,
        registry: Dict[str, dict],
    ) -> Dict[str, dict]:
        """Return the subset of *registry* that should appear in the prompt.

        Parameters
        ----------
        user_message:
            The current user turn (used for keyword matching).
        registry:
            The full ``_TOOL_REGISTRY`` dict mapping tool names → metadata.

        Returns
        -------
        dict
            Filtered copy of *registry* containing only active tools.
        """
        active_names: Set[str] = set()
        activated_bundles: list[str] = []

        for name, bundle in self._bundles.items():
            state = self._state[name]

            if bundle.policy == ActivationPolicy.ALWAYS:
                active_names.update(bundle.tools)
                activated_bundles.append(name)
                continue

            if bundle.policy == ActivationPolicy.SESSION:
                if state.activated:
                    active_names.update(bundle.tools)
                    activated_bundles.append(name)
                    continue
                # Warm-window: check if any tool in the bundle was used recently
                if self._was_used_recently(bundle):
                    state.activated = True
                    active_names.update(bundle.tools)
                    activated_bundles.append(name)
                    continue
                # Also activate if keywords match (session bundles can have keywords)
                if bundle.keywords and self._keywords_match(
                    bundle.keywords, user_message
                ):
                    active_names.update(bundle.tools)
                    activated_bundles.append(name)
                    continue

            if bundle.policy == ActivationPolicy.KEYWORD:
                if state.activated:
                    # Already activated this session — keep warm
                    active_names.update(bundle.tools)
                    activated_bundles.append(name)
                    continue
                if bundle.keywords and self._keywords_match(
                    bundle.keywords, user_message
                ):
                    active_names.update(bundle.tools)
                    activated_bundles.append(name)
                    continue
                # Warm-window fallback
                if self._was_used_recently(bundle):
                    active_names.update(bundle.tools)
                    activated_bundles.append(name)
                    continue

        # Include any registered tools that are NOT in any bundle (backwards compat).
        unbundled = {t for t in registry if t not in self._tool_to_bundle}
        active_names.update(unbundled)

        logger.debug(
            "ToolLoader resolved %d/%d tools (bundles: %s)",
            len(active_names & set(registry)),
            len(registry),
            ", ".join(activated_bundles) or "none",
        )

        return {k: v for k, v in registry.items() if k in active_names}

    # ── tool execution hook ──────────────────────────────────────────────

    def record_tool_use(self, tool_name: str) -> None:
        """Record that a tool was executed (called from ``_execute_tool``).

        This flips the owning bundle's ``activated`` flag so session-policy
        bundles stay warm for the rest of the conversation.
        """
        now = time.time()
        self._tool_history.append((tool_name, now))
        bundle_name = self._tool_to_bundle.get(tool_name)
        if bundle_name and bundle_name in self._state:
            self._state[bundle_name].activated = True
            self._state[bundle_name].last_used_ts = now

    # ── query helpers ────────────────────────────────────────────────────

    def get_active_bundle_names(self) -> list[str]:
        """Return names of currently activated bundles."""
        return [n for n, s in self._state.items() if s.activated]

    def get_bundle_for_tool(self, tool_name: str) -> Optional[str]:
        """Return the bundle name that owns *tool_name*, or ``None``."""
        return self._tool_to_bundle.get(tool_name)

    def force_activate(self, bundle_name: str) -> None:
        """Force-activate a bundle for the current session.

        This is a public API for callers that need to mark a bundle active
        without reaching into ``_state`` directly.
        """
        if bundle_name not in self._state:
            raise KeyError(f"Unknown bundle: {bundle_name}")
        now = time.time()
        self._state[bundle_name].activated = True
        self._state[bundle_name].last_used_ts = now

    def reset_session(self) -> None:
        """Clear per-session state (call between conversations)."""
        for state in self._state.values():
            state.activated = False
            state.last_used_ts = 0.0
        self._tool_history.clear()

    # ── internals ────────────────────────────────────────────────────────

    def _keywords_match(self, keywords: FrozenSet[str], message: str) -> bool:
        """Return True if any keyword pattern matches *message*."""
        for pattern in keywords:
            try:
                if re.search(pattern, message, re.IGNORECASE):
                    return True
            except re.error:
                # Treat bad regex as a plain substring match
                if pattern.lower() in message.lower():
                    return True
        return False

    def _was_used_recently(self, bundle: ToolBundle) -> bool:
        """Check if any tool in *bundle* was used within the warm window."""
        cutoff = time.time() - self.WARM_WINDOW_SECS
        for tool_name, ts in reversed(self._tool_history):
            if ts < cutoff:
                break
            if tool_name in bundle.tools:
                return True
        return False
