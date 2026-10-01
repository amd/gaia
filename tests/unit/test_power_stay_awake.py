# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""GAIA keeps a Modern Standby PC awake while it works, then lets it sleep."""

import sys

import pytest

from gaia.utils import power

pytestmark = pytest.mark.skipif(
    sys.platform != "win32", reason="Windows power requests"
)

HOLD = 0x80000000 | 0x1 | 0x2
RELEASE = 0x80000000


@pytest.fixture
def calls(monkeypatch):
    import ctypes

    seen = []
    monkeypatch.setattr(
        ctypes.windll.kernel32,
        "SetThreadExecutionState",
        lambda flags: seen.append(flags) or 1,
    )
    return seen


def test_the_request_is_held_for_the_work_and_released_after(calls):
    with power.stay_awake():
        assert calls == [HOLD]
    assert calls == [HOLD, RELEASE]


def test_an_inner_holder_does_not_release_the_outer_one(calls):
    """An eval runs agent turns inside its own hold on the same thread."""
    with power.stay_awake(required=True):
        with power.stay_awake():
            pass
        assert calls == [HOLD]
    assert calls == [HOLD, RELEASE]


def test_a_refused_request_is_fatal_only_when_required(monkeypatch, caplog):
    import ctypes

    monkeypatch.setattr(ctypes.windll.kernel32, "SetThreadExecutionState", lambda f: 0)
    with pytest.raises(OSError, match="stay awake"):
        with power.stay_awake(required=True):
            pass
    ran = False
    with power.stay_awake():
        ran = True
    assert ran
    assert "stay awake" in caplog.text


def test_an_agent_turn_holds_the_request(calls, monkeypatch):
    import contextlib

    from gaia.agents.base.agent import Agent

    class _Bare(Agent):
        def _register_tools(self):
            pass

    seen_during = []
    agent = _Bare.__new__(_Bare)
    monkeypatch.setattr(
        agent,
        "_agent_identity_context",
        lambda _id: contextlib.nullcontext(),
        raising=False,
    )
    monkeypatch.setattr(agent, "_namespaced_agent_id", lambda: "t", raising=False)
    monkeypatch.setattr(
        agent,
        "_process_query_impl",
        lambda *a: seen_during.append(list(calls)) or {"result": "ok"},
        raising=False,
    )
    monkeypatch.setattr(agent, "_finish_turn_record", lambda *a: None, raising=False)
    monkeypatch.setattr(agent, "_detach_step_timer", lambda: None, raising=False)
    assert agent.process_query("hi") == {"result": "ok"}
    assert seen_during == [[HOLD]]
    assert calls == [HOLD, RELEASE]
