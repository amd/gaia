# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""The ``sleep`` tool: waits for real, refuses bad durations, and yields to Stop.

Every test runs on a fake clock, so none of them actually waits.
"""

from __future__ import annotations

import threading
from unittest.mock import MagicMock, patch

import pytest

from gaia.agents.base.agent import (
    TOOLS_REQUIRING_CONFIRMATION,
    TOOLS_WITHOUT_EXTERNAL_CONTENT,
    Agent,
)
from gaia.agents.base.tools import _TOOL_REGISTRY
from gaia.agents.registry import KNOWN_TOOLS
from gaia.agents.tools import wait_tools
from gaia.agents.tools.wait_tools import MAX_SLEEP_SECONDS, WaitToolsMixin
from gaia.tool_cancellation import set_tool_cancel_event


class _WaitAgent(Agent, WaitToolsMixin):
    def _get_system_prompt(self) -> str:
        return ""

    def _register_tools(self) -> None:
        self.register_wait_tools()


class _FakeClock:
    """A monotonic clock that only moves when something sleeps on it."""

    def __init__(self):
        self.elapsed = 0.0
        self.naps: list[float] = []
        self.on_nap = None

    def monotonic(self) -> float:
        return 1000.0 + self.elapsed

    def sleep(self, seconds: float) -> None:
        self.naps.append(seconds)
        self.elapsed += seconds
        if self.on_nap is not None:
            self.on_nap(self.elapsed)


@pytest.fixture
def agent():
    saved = dict(_TOOL_REGISTRY)
    _TOOL_REGISTRY.clear()
    try:
        with patch("gaia.agents.base.agent.AgentSDK"):
            yield _WaitAgent(skip_lemonade=True, silent_mode=True)
    finally:
        _TOOL_REGISTRY.clear()
        _TOOL_REGISTRY.update(saved)


@pytest.fixture
def clock(monkeypatch):
    fake = _FakeClock()
    monkeypatch.setattr(wait_tools.time, "monotonic", fake.monotonic)
    monkeypatch.setattr(wait_tools.time, "sleep", fake.sleep)
    return fake


def _sleep(**kwargs):
    return _TOOL_REGISTRY["sleep"]["function"](**kwargs)


# ── waiting ────────────────────────────────────────────────────────────────


@pytest.mark.usefixtures("agent")
def test_sleeps_the_requested_time(clock):
    result = _sleep(seconds=42, reason="GitHub rate limit resets at 19:43")

    assert result["status"] == "success"
    assert result["slept_seconds"] == pytest.approx(42)
    assert result["reason"] == "GitHub rate limit resets at 19:43"
    assert result["finished_at"]
    assert sum(clock.naps) == pytest.approx(42)
    # Short naps, so a Stop is noticed within a second rather than at the end.
    assert max(clock.naps) <= 1.0


@pytest.mark.usefixtures("clock")
def test_a_number_sent_as_a_string_still_waits(agent):
    """Models send ``"30"`` for a float; the real dispatch path coerces it."""
    result = agent._execute_tool("sleep", {"seconds": "30"})

    assert result["status"] == "success"
    assert result["slept_seconds"] == pytest.approx(30)


def test_a_full_length_wait_outlives_the_default_tool_timeout(agent):
    """The default per-tool timeout (180 s) would abandon a legal 300 s wait."""
    assert agent._resolve_tool_timeout("sleep") > MAX_SLEEP_SECONDS


# ── refused durations ──────────────────────────────────────────────────────


@pytest.mark.usefixtures("agent")
@pytest.mark.parametrize("seconds", [MAX_SLEEP_SECONDS + 1, 3600])
def test_over_the_cap_is_refused_not_clamped(clock, seconds):
    result = _sleep(seconds=seconds)

    assert result["status"] == "error"
    assert f"{MAX_SLEEP_SECONDS:g}" in result["error"]  # states the limit
    assert "again" in result["error"]  # and how to wait longer
    assert clock.naps == []


@pytest.mark.usefixtures("agent")
@pytest.mark.parametrize("seconds", [0, -5, float("nan"), float("inf")])
def test_a_non_positive_or_non_finite_wait_is_refused(clock, seconds):
    result = _sleep(seconds=seconds)

    assert result["status"] == "error"
    assert "greater than 0" in result["error"]
    assert clock.naps == []


# ── Stop ───────────────────────────────────────────────────────────────────


def _arm_console(agent):
    """The Agent UI's Stop and the TUI's ``cancel`` both set this flag."""
    agent.console.cancelled = threading.Event()
    return agent.console.cancelled


def _arm_cancel_event(agent):
    """The flagship's ``/cancel`` and the UI's stream teardown set this one."""
    agent._cancel_event = threading.Event()
    return agent._cancel_event


def _arm_tool_timeout(_agent):
    """Set when the agent stops waiting on the tool call itself."""
    event = threading.Event()
    set_tool_cancel_event(event)
    return event


@pytest.mark.parametrize(
    "arm",
    [_arm_console, _arm_cancel_event, _arm_tool_timeout],
    ids=lambda f: f.__name__,
)
def test_a_stop_ends_the_wait_early(agent, clock, arm):
    stop = arm(agent)
    clock.on_nap = lambda elapsed: elapsed >= 3 and stop.set()
    try:
        result = _sleep(seconds=MAX_SLEEP_SECONDS, reason="rate limit")
    finally:
        set_tool_cancel_event(None)

    assert result["status"] == "cancelled"
    assert result["slept_seconds"] == pytest.approx(3)
    assert result["requested_seconds"] == MAX_SLEEP_SECONDS
    assert "stop" in result["message"].lower()


def test_a_turn_already_stopped_does_not_wait_at_all(agent, clock):
    _arm_cancel_event(agent).set()

    result = _sleep(seconds=60)

    assert result["status"] == "cancelled"
    assert clock.naps == []


# ── gating and provenance ─────────────────────────────────────────────────


@pytest.mark.usefixtures("clock")
def test_is_not_confirmation_gated(agent):
    agent.console.confirm_tool_execution = MagicMock(return_value=False)

    result = agent._execute_tool("sleep", {"seconds": 5})

    assert "sleep" not in TOOLS_REQUIRING_CONFIRMATION
    assert not agent._tool_requires_confirmation("sleep")
    agent.console.confirm_tool_execution.assert_not_called()
    assert result["status"] == "success"


@pytest.mark.usefixtures("clock")
def test_a_wait_is_a_receipt_not_external_content(agent):
    """Waiting brings nothing into the turn, so it must not taint provenance."""
    agent._turn_saw_external_content = False

    result = agent._execute_tool("sleep", {"seconds": 2, "reason": "rate limit"})

    assert result["status"] == "success"
    assert "sleep" in TOOLS_WITHOUT_EXTERNAL_CONTENT
    assert agent.turn_content_provenance() == "user_instruction"


def test_registered_as_a_composable_mixin():
    assert KNOWN_TOOLS["wait"] == ("gaia.agents.tools.wait_tools", "WaitToolsMixin")
