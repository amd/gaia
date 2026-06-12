# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""
Tests for the bounded, cancellable agent tool-execution guard.

Background (issue #1579): the Agent UI had no per-tool or per-agent execution
timeout. When a tool blocked (e.g. a hung connector token call), the base
``Agent`` loop sat in ``tool(**tool_args)`` forever and the producer thread
that runs ``process_query`` leaked. These tests pin the two-part fix:

1. ``Agent._execute_tool`` bounds every tool call. A tool that blocks past the
   limit surfaces a *fail-loud, actionable* error within a sensible window
   (not a 10-minute hang) instead of blocking the loop.
2. The ``process_query`` loop honours ``self._cancel_event`` at each step
   boundary, so a producer thread can be torn down rather than leaked once the
   consumer signals cancellation.
"""

import threading
import time
from unittest.mock import MagicMock, patch

import pytest

from gaia.agents.base.agent import (
    DEFAULT_TOOL_TIMEOUT,
    Agent,
    tool_execution_timeout,
)
from gaia.agents.base.tools import _TOOL_REGISTRY, tool


@pytest.fixture(autouse=True)
def _clean_registry():
    """Save/restore the global tool registry around each test."""
    saved = dict(_TOOL_REGISTRY)
    yield
    _TOOL_REGISTRY.clear()
    _TOOL_REGISTRY.update(saved)


@pytest.fixture(autouse=True)
def _clear_timeout_env(monkeypatch):
    """Default to an unset GAIA_AGENT_TOOL_TIMEOUT for deterministic tests."""
    monkeypatch.delenv("GAIA_AGENT_TOOL_TIMEOUT", raising=False)


class _TimeoutAgent(Agent):
    """Minimal concrete Agent that pulls tools from the global registry."""

    def _register_tools(self):  # tools are registered per-test via @tool
        pass


def _make_agent(**kwargs) -> _TimeoutAgent:
    """Construct a real Agent without touching Lemonade or the network."""
    with patch("gaia.agents.base.agent.AgentSDK"):
        return _TimeoutAgent(skip_lemonade=True, silent_mode=True, **kwargs)


# ─────────────────────────── tool_execution_timeout() ────────────────────────


class TestTimeoutConfig:
    def test_default_when_unset(self):
        assert tool_execution_timeout() == DEFAULT_TOOL_TIMEOUT

    def test_valid_override(self, monkeypatch):
        monkeypatch.setenv("GAIA_AGENT_TOOL_TIMEOUT", "45")
        assert tool_execution_timeout() == 45.0

    def test_fractional_override(self, monkeypatch):
        monkeypatch.setenv("GAIA_AGENT_TOOL_TIMEOUT", "0.5")
        assert tool_execution_timeout() == 0.5

    @pytest.mark.parametrize("bad", ["notanumber", "0", "-5"])
    def test_invalid_raises(self, monkeypatch, bad):
        """A present-but-invalid value fails loudly (no silent fallback)."""
        monkeypatch.setenv("GAIA_AGENT_TOOL_TIMEOUT", bad)
        with pytest.raises(ValueError):
            tool_execution_timeout()


# ─────────────────────────── per-tool timeout guard ──────────────────────────


class TestBlockingToolBounded:
    def test_blocking_tool_returns_actionable_error_fast(self, monkeypatch):
        """A tool that blocks past the limit yields a bounded, actionable error."""
        release = threading.Event()

        @tool
        def hangs_forever() -> dict:
            """Simulates a hung connector/network call."""
            release.wait(timeout=30)
            return {"status": "ok"}

        monkeypatch.setenv("GAIA_AGENT_TOOL_TIMEOUT", "0.3")
        agent = _make_agent()

        try:
            t0 = time.monotonic()
            result = agent._execute_tool("hangs_forever", {})
            elapsed = time.monotonic() - t0

            # Bounded — nowhere near the 30s tool block or the 600s consumer cap.
            assert elapsed < 5.0
            assert result["status"] == "error"
            assert result.get("timeout") is True
            # Actionable: names the offending tool and the override knob.
            assert "hangs_forever" in result["error"]
            assert "GAIA_AGENT_TOOL_TIMEOUT" in result["error"]
        finally:
            release.set()

    def test_fast_tool_is_unaffected(self, monkeypatch):
        """The guard is transparent for tools that finish in time."""
        monkeypatch.setenv("GAIA_AGENT_TOOL_TIMEOUT", "5")

        @tool
        def quick(value: str) -> dict:
            """Returns immediately."""
            return {"echo": value.upper()}

        agent = _make_agent()
        result = agent._execute_tool("quick", {"value": "hi"})
        assert result == {"echo": "HI"}

    def test_tool_exception_still_surfaces(self, monkeypatch):
        """An exception raised inside the worker is reported, not swallowed."""
        monkeypatch.setenv("GAIA_AGENT_TOOL_TIMEOUT", "5")

        @tool
        def explodes() -> dict:
            """Raises to exercise the non-timeout error path."""
            raise RuntimeError("boom")

        agent = _make_agent()
        result = agent._execute_tool("explodes", {})
        assert result["status"] == "error"
        assert result.get("timeout") is not True
        assert "boom" in (result.get("error_brief") or result.get("error") or "")


class TestPerToolOverride:
    def test_decorator_stores_timeout(self):
        @tool(timeout=0.3)
        def slow_but_declared() -> dict:
            """Declares its own (short) timeout."""
            return {"ok": True}

        assert _TOOL_REGISTRY["slow_but_declared"]["timeout"] == 0.3

    def test_generate_image_opts_out_of_global_cap(self, tmp_path):
        """SD image generation declares a long timeout (model download)."""
        from gaia.sd.mixin import SDToolsMixin

        mixin = SDToolsMixin()
        mixin.init_sd(output_dir=str(tmp_path))

        # Resolved through the real Agent path: a tool the global default
        # would cap at 180s must keep its declared 900s window.
        agent = _make_agent()
        assert agent._resolve_tool_timeout("generate_image") == 900.0

    def test_long_running_tools_opt_out_of_global_cap(self):
        """Heavy index/summarize tools keep generous timeouts, not the 180s cap.

        These write to shared FAISS/DB state and can legitimately run minutes;
        the default cap would abandon a valid operation mid-write.
        """
        from gaia.agents.tools.code_index_tools import CodeIndexToolsMixin
        from gaia.agents.tools.rag_tools import RAGToolsMixin

        RAGToolsMixin.__new__(RAGToolsMixin).register_rag_tools()
        CodeIndexToolsMixin.__new__(CodeIndexToolsMixin).register_code_index_tools()

        agent = _make_agent()
        expected = {
            "index_document": 600.0,
            "summarize_document": 600.0,
            "index_directory": 900.0,
            "index_codebase": 900.0,
        }
        for name, want in expected.items():
            assert agent._resolve_tool_timeout(name) == want, name

    def test_per_tool_timeout_beats_global(self, monkeypatch):
        """A tool's own short timeout fires even when the global cap is large."""
        release = threading.Event()

        @tool(timeout=0.3)
        def hangs_with_override() -> dict:
            """Blocks, but declares a short per-tool timeout."""
            release.wait(timeout=30)
            return {"status": "ok"}

        # Global default is large; only the per-tool override keeps us bounded.
        monkeypatch.setenv("GAIA_AGENT_TOOL_TIMEOUT", "60")
        agent = _make_agent()
        try:
            t0 = time.monotonic()
            result = agent._execute_tool("hangs_with_override", {})
            elapsed = time.monotonic() - t0
            assert elapsed < 5.0
            assert result.get("timeout") is True
        finally:
            release.set()


# ─────────────────────────── cancellable producer ────────────────────────────


class TestProducerCancellation:
    def _drive(self, agent, send_side_effect):
        """Wire a mocked LLM step and return the producer thread + holder."""
        agent.chat = MagicMock()
        agent.chat.send_messages.side_effect = send_side_effect
        holder = {}

        def _producer():
            holder["result"] = agent.process_query("hello")

        thread = threading.Thread(target=_producer, name="producer", daemon=True)
        return thread, holder

    def test_cancel_before_start_returns_without_llm_call(self):
        """A pre-set cancel event stops the loop before any LLM round-trip."""
        agent = _make_agent(max_steps=10)
        agent._cancel_event = threading.Event()
        agent._cancel_event.set()

        thread, holder = self._drive(
            agent, send_side_effect=AssertionError("LLM should not be called")
        )
        thread.start()
        thread.join(3.0)

        assert not thread.is_alive()
        agent.chat.send_messages.assert_not_called()

    def test_cancel_mid_run_tears_down_producer(self):
        """Cancelling mid-run stops the loop at the next step boundary."""
        agent = _make_agent(max_steps=10)
        agent._cancel_event = threading.Event()

        def _send(*_args, **_kwargs):
            # Signal cancellation, then hand back an (empty) response. The loop
            # must not spin to max_steps — it should break on the next boundary.
            agent._cancel_event.set()
            return MagicMock(text="", stats=None)

        thread, _holder = self._drive(agent, send_side_effect=_send)
        thread.start()
        thread.join(5.0)

        assert not thread.is_alive(), "producer thread outlived the request"
        assert agent.chat.send_messages.call_count == 1
