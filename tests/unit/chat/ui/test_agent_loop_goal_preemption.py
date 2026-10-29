# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Cancellation and foreground preemption for autonomous goal ticks (#6006)."""

import threading
from types import SimpleNamespace
from unittest.mock import MagicMock

from gaia.ui.agent_loop import AgentLoop


def _active_tick():
    cancel = threading.Event()
    handler = SimpleNamespace(
        cancelled=threading.Event(),
        close_active_relay_response=MagicMock(),
    )
    return (cancel, handler)


def test_cancel_goal_reaches_the_live_tick_and_wakes_relay():
    loop = AgentLoop()
    active = _active_tick()
    loop._active_goal_ticks["goal-1"] = active

    assert loop.cancel_goal("goal-1") is True

    cancel, handler = active
    assert cancel.is_set()
    assert handler.cancelled.is_set()
    handler.close_active_relay_response.assert_called_once_with()


def test_cancel_unknown_goal_does_not_touch_other_ticks():
    loop = AgentLoop()
    active = _active_tick()
    loop._active_goal_ticks["goal-1"] = active

    assert loop.cancel_goal("missing") is False

    cancel, handler = active
    assert not cancel.is_set()
    assert not handler.cancelled.is_set()
    handler.close_active_relay_response.assert_not_called()


def test_yield_to_user_stops_each_active_tick_once():
    loop = AgentLoop()
    first = _active_tick()
    second = _active_tick()
    loop._active_goal_ticks.update({"goal-1": first, "goal-2": first, "goal-3": second})

    assert loop.yield_to_user() is True

    for cancel, handler in (first, second):
        assert cancel.is_set()
        assert handler.cancelled.is_set()
        handler.close_active_relay_response.assert_called_once_with()
