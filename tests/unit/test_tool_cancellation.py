# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Cooperative cancellation for tools the agent has stopped waiting for (#2600)."""

import logging
import threading
import time

import pytest

from gaia.agents.base.tools import (
    AbandonedWorkerLogFilter,
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


class TestAbandonedWorkerLogIsolation:
    """A timed-out worker's later log calls must not reach a later caller's
    log-capture window (#2600).

    The worker uses ``gaia.database.mixin`` -- the exact logger the original
    CI failure leaked from -- to show the filter works regardless of which
    module the abandoned tool body happens to log through.
    """

    LEAK_LOGGER = "gaia.database.mixin"

    def teardown_method(self):
        set_tool_cancel_event(None)

    def _run_past_timeout_then_log(self, message: str):
        """Start a tool that overruns its timeout, then have the abandoned
        worker log *after* the caller has already moved on, mirroring the
        real bug: the emitting call happens once nothing is waiting on it.
        """
        from gaia.agents.base.agent import Agent, ToolExecutionTimeout

        logged = threading.Event()

        def slow_tool():
            time.sleep(0.2)  # overrun the 0.05s window below
            logging.getLogger(self.LEAK_LOGGER).info(message)
            logged.set()
            return "finished late"

        class _StubAgent(Agent):
            def _register_tools(self):
                pass

        agent = object.__new__(_StubAgent)
        agent._resolve_tool_timeout = lambda _name: 0.05

        with pytest.raises(ToolExecutionTimeout):
            Agent._call_tool_bounded(agent, slow_tool, {}, "slow_tool")

        assert logged.wait(5), "abandoned worker never reached its log call"

    def test_abandoned_worker_log_is_dropped_when_filter_is_attached(self, caplog):
        """Reproduces #2600: attach the filter to the capture handler exactly
        as GaiaLogger attaches it to the root console/file handlers, then
        prove the late record from the abandoned worker never arrives.

        pytest reuses a single ``LogCaptureHandler`` for the whole session
        (only its records are reset between tests, not its filters), so the
        filter is removed again in ``finally`` -- otherwise it would silently
        suppress every other test's log assertions too.
        """
        caplog.set_level(logging.INFO, logger=self.LEAK_LOGGER)
        log_filter = AbandonedWorkerLogFilter()
        caplog.handler.addFilter(log_filter)
        try:
            self._run_past_timeout_then_log("zombie worker wrote this")

            leaked = [r for r in caplog.records if r.name == self.LEAK_LOGGER]
            assert not leaked, f"abandoned worker's log record leaked: {leaked}"

            # A later, unrelated caller on the SAME logger still logs
            # normally -- the filter only silences the abandoned thread.
            logging.getLogger(self.LEAK_LOGGER).info("unrelated later caller")
            later = [r for r in caplog.records if r.name == self.LEAK_LOGGER]
            assert len(later) == 1
            assert later[0].getMessage() == "unrelated later caller"
        finally:
            caplog.handler.removeFilter(log_filter)

    def test_abandoned_worker_log_leaks_without_the_filter(self, caplog):
        """Baseline: without the filter attached, the bug from #2600
        reproduces -- the zombie worker's record lands in this window.
        """
        caplog.set_level(logging.INFO, logger=self.LEAK_LOGGER)

        self._run_past_timeout_then_log("zombie worker wrote this too")

        leaked = [r for r in caplog.records if r.name == self.LEAK_LOGGER]
        assert leaked, "expected the unfiltered baseline to reproduce the leak"
