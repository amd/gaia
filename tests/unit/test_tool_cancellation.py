# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Cooperative cancellation for tools the agent has stopped waiting for (#2600)."""

import threading
import time

import pytest

from gaia.agents.base.tools import (
    ToolCancelled,
    raise_if_cancelled,
    set_tool_cancel_event,
    tool_cancelled,
)


class TestCancellationFlag:
    def teardown_method(self):
        set_tool_cancel_event(None)

    def test_unset_thread_is_never_cancelled(self):
        """Tools called outside the agent loop must not think they're cancelled."""
        set_tool_cancel_event(None)
        assert tool_cancelled() is False
        raise_if_cancelled()

    def test_reflects_the_bound_event(self):
        event = threading.Event()
        set_tool_cancel_event(event)

        assert tool_cancelled() is False
        event.set()
        assert tool_cancelled() is True

        with pytest.raises(ToolCancelled):
            raise_if_cancelled()

    def test_flag_is_per_thread(self):
        """One tool's cancellation must not abort another thread's work."""
        event = threading.Event()
        event.set()
        set_tool_cancel_event(event)

        other = {}

        def _worker():
            other["cancelled"] = tool_cancelled()

        thread = threading.Thread(target=_worker)
        thread.start()
        thread.join()

        assert tool_cancelled() is True
        assert other["cancelled"] is False


class TestBoundedCallSetsCancellation:
    """``_call_tool_bounded`` must signal the worker, not just abandon it."""

    def _agent(self, timeout):
        """A bare Agent instance — __init__ is far too heavy for this path."""
        from gaia.agents.base.agent import Agent

        class _StubAgent(Agent):
            def _register_tools(self):
                pass

        agent = object.__new__(_StubAgent)
        agent._resolve_tool_timeout = lambda _name: timeout
        return agent

    def test_overrunning_tool_is_told_it_was_cancelled(self):
        from gaia.agents.base.agent import Agent, ToolExecutionTimeout

        observed = {}
        released = threading.Event()

        def slow_tool():
            # Runs past its window, then checks whether anyone still cares.
            for _ in range(200):
                if tool_cancelled():
                    observed["cancelled"] = True
                    released.set()
                    return "aborted"
                time.sleep(0.01)
            observed["cancelled"] = False
            released.set()
            return "finished"

        agent = self._agent(0.05)
        with pytest.raises(ToolExecutionTimeout):
            Agent._call_tool_bounded(agent, slow_tool, {}, "slow_tool")

        assert released.wait(5), "worker never observed the cancellation flag"
        assert observed["cancelled"] is True

    def test_fast_tool_is_unaffected(self):
        from gaia.agents.base.agent import Agent

        def quick_tool():
            assert tool_cancelled() is False
            return "ok"

        agent = self._agent(30)
        assert Agent._call_tool_bounded(agent, quick_tool, {}, "quick_tool") == "ok"

    def test_worker_clears_the_flag_so_the_thread_can_be_reused(self):
        from gaia.agents.base.agent import Agent

        seen = {}

        def tool():
            seen["during"] = tool_cancelled()
            return "ok"

        agent = self._agent(30)
        Agent._call_tool_bounded(agent, tool, {}, "tool")

        assert seen["during"] is False
        assert tool_cancelled() is False
