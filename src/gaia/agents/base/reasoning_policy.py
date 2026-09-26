# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Decide, step by step, whether the model may spend tokens on reasoning.

Measured on the benchmark runs, 58-85% of every output token is the model's
reasoning, and output is priced 4-5x input: reasoning is about 40% of the bill
and most of the model time per step. Much of it is spent deciding what to do
after a file read whose next move was already obvious.

The policy is a deterministic rule over the *previous* step's tool results,
decided before the call. ``off`` sends nothing (the model's own default).
``none`` sends ``reasoning_effort="none"`` on every step. ``adaptive`` keeps
reasoning on where a decision is being made -- the first step of a turn, the
step after a tool error, a test-runner summary, an edit, or a delegated
subtask -- and turns it off after a plain read, search, list or shell look.
"""

from __future__ import annotations

import os
from typing import Any, Dict, Optional, Tuple

from gaia.agents.base.checks import declares_check
from gaia.agents.base.verification import (
    check_output,
    has_test_run_summary,
    is_mutating_tool,
)

REASONING_ENV_VAR = "GAIA_REASONING"
REASONING_POLICIES: Tuple[str, ...] = ("off", "none", "adaptive")

#: Whether each step's reasoning is re-sent with the assistant message on the
#: steps that follow it, within one request. Measured at 14-24% of all prompt
#: tokens over a run; the conversation log keeps it either way.
REASONING_HISTORY_ENV_VAR = "GAIA_REASONING_HISTORY"
REASONING_HISTORY_MODES: Tuple[str, ...] = ("send", "drop")

#: Sent to the backend when the policy turns reasoning off for a step.
REASONING_EFFORT_OFF = "none"

#: Stats-record value when nothing is sent and the model reasons as it likes.
REASONING_DEFAULT = "default"

#: A result from this tool is a worker's whole subtask; deciding what to do
#: with it is worth thinking about.
DELEGATE_TOOL = "delegate_task"


def reasoning_policy_from_env() -> Optional[str]:
    """``GAIA_REASONING`` as a policy, or ``None`` when unset; malformed fails loudly."""
    raw = os.getenv(REASONING_ENV_VAR)
    if raw is None:
        return None
    value = raw.strip().lower()
    if value in REASONING_POLICIES:
        return value
    raise ValueError(
        f"{REASONING_ENV_VAR} must be one of {', '.join(REASONING_POLICIES)}, "
        f"got {raw!r}"
    )


def reasoning_history_from_env() -> Optional[str]:
    """``GAIA_REASONING_HISTORY`` as a mode, or ``None`` when unset; malformed fails loudly."""
    raw = os.getenv(REASONING_HISTORY_ENV_VAR)
    if raw is None:
        return None
    value = raw.strip().lower()
    if value in REASONING_HISTORY_MODES:
        return value
    raise ValueError(
        f"{REASONING_HISTORY_ENV_VAR} must be one of "
        f"{', '.join(REASONING_HISTORY_MODES)}, got {raw!r}"
    )


def validate_reasoning_history(mode: Any) -> str:
    """*mode* as one of :data:`REASONING_HISTORY_MODES`, or a ``ValueError``."""
    if not isinstance(mode, str) or mode not in REASONING_HISTORY_MODES:
        raise ValueError(
            f"reasoning_history must be one of {', '.join(REASONING_HISTORY_MODES)}, "
            f"got {mode!r}."
        )
    return mode


def validate_reasoning_policy(policy: Any) -> str:
    """*policy* as one of :data:`REASONING_POLICIES`, or a ``ValueError``."""
    if not isinstance(policy, str) or policy not in REASONING_POLICIES:
        raise ValueError(
            f"reasoning_policy must be one of {', '.join(REASONING_POLICIES)}, "
            f"got {policy!r}."
        )
    return policy


def tool_result_failed(result: Any) -> bool:
    """The agent loop's own test for an errored tool result, in one place."""
    return isinstance(result, dict) and (
        result.get("status") == "error"
        or result.get("success") is False
        or result.get("has_errors") is True
        or result.get("return_code", 0) != 0
    )


def result_needs_reasoning(tool_name: str, result: Any) -> bool:
    """True when the step after this result is one the model should think about."""
    name = (tool_name or "").strip()
    if tool_result_failed(result):
        return True
    # A refused or declined call is an obstacle to route around, like an error.
    if isinstance(result, dict) and (
        result.get("status") == "denied" or result.get("ran") is False
    ):
        return True
    if name == DELEGATE_TOOL or is_mutating_tool(name):
        return True
    if declares_check(result) and result.get("check_result") is not None:
        return True
    return has_test_run_summary(check_output(name, result))


class ReasoningPolicy:
    """One agent's rule for the ``reasoning_effort`` of each step's request."""

    def __init__(self, policy: str) -> None:
        self.policy = validate_reasoning_policy(policy)
        self._step_started = False
        self._observed: int = 0
        self._needs_reasoning = False

    @property
    def active(self) -> bool:
        """False for ``off``: nothing is sent and nothing is recorded."""
        return self.policy != "off"

    def begin_turn(self) -> None:
        self._step_started = False
        self._observed = 0
        self._needs_reasoning = False

    def observe(self, tool_name: str, result: Any) -> None:
        """Note one tool result of the current step."""
        if not self.active:
            return
        self._observed += 1
        if result_needs_reasoning(tool_name, result):
            self._needs_reasoning = True

    def decide(self) -> Optional[str]:
        """The ``reasoning_effort`` for the next call, or ``None`` to send nothing.

        Called at the head of each step; the observations it reads are the
        previous step's, and it clears them for the step that follows.
        """
        if not self.active:
            return None
        if self.policy == "none":
            return REASONING_EFFORT_OFF
        first_step = not self._step_started
        self._step_started = True
        observed, needs = self._observed, self._needs_reasoning
        self._observed = 0
        self._needs_reasoning = False
        # A step that ran no tool (a parse error, a bare reply) settles nothing,
        # so the next one keeps the model's default rather than guessing.
        if first_step or observed == 0 or needs:
            return None
        return REASONING_EFFORT_OFF

    @staticmethod
    def request_kwargs(effort: Optional[str]) -> Dict[str, Any]:
        """The request fields for a decided *effort*: empty when nothing is sent."""
        return {"reasoning_effort": effort} if effort is not None else {}

    @staticmethod
    def recorded(effort: Optional[str]) -> str:
        """What the step's stats record says was sent."""
        return effort if effort is not None else REASONING_DEFAULT
