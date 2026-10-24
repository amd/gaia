# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""The agent's follow-up tools and the per-turn line that reports results.

The daemon client is stubbed; the daemon side is in test_daemon_followups.py.
"""

from __future__ import annotations

import time

import pytest

from gaia.agents.tools.follow_up_tools import FollowUpToolsMixin
from gaia.daemon import client
from gaia.daemon.errors import DaemonError


class _Host(FollowUpToolsMixin):
    _memory_session_id = "s-1"
    model_id = "fireworks.deepseek-v4p1-flash"


class _Calls(list):
    state: dict


@pytest.fixture
def calls(monkeypatch):
    log = _Calls()
    state = {"results": [], "pending": []}

    def request(method, path="", *, json=None, params=None, timeout=10.0, start=True):
        log.append({"method": method, "path": path, "json": json, "start": start})
        if method == "GET":
            return {"pending": state["pending"], "results": state["results"]}
        if method == "POST" and path == "":
            return {"id": "j1", "task": json["task"], "fire_at": json["fire_at"]}
        if path == "/seen":
            state["results"] = []
            return {"seen": len(json["ids"])}
        return {"id": path.split("/")[1], "status": "cancelled"}

    monkeypatch.setattr(client, "followups_request", request)
    log.state = state
    return log


def test_scheduling_sends_the_time_and_says_when_it_will_run(calls):
    out = _Host()._schedule_follow_up("in 2 hours", "Check CI run 4012")

    assert out["status"] == "scheduled" and out["id"] == "j1"
    sent = calls[0]["json"]
    assert sent["agent_id"] == "gaia" and sent["task"] == "Check CI run 4012"
    assert 2 * 3600 - 60 < sent["fire_at"] - time.time() <= 2 * 3600
    assert sent["origin"] == {"session": "s-1"}
    assert sent["model"] == "fireworks.deepseek-v4p1-flash"
    assert "Tell the user when it will run" in out["message"]


def test_a_time_it_cannot_read_is_refused_before_any_call(calls):
    out = _Host()._schedule_follow_up("next blue moon", "x")
    assert out["status"] == "error" and "in 2 hours" in out["error"]
    assert calls == []


def test_a_refusal_from_the_daemon_reaches_the_model(monkeypatch):
    def refuse(*a, **k):
        raise DaemonError("10 follow-ups are already waiting. Cancel one first.")

    monkeypatch.setattr(client, "followups_request", refuse)
    out = _Host()._schedule_follow_up("in 2 hours", "x")
    assert out["status"] == "error" and "already waiting" in out["error"]


def test_the_turn_reports_finished_follow_ups_once(calls):
    host = _Host()
    host._follow_ups_enabled = True
    calls.state["results"] = [
        {
            "job_id": "j1",
            "task": "Check CI run 4012",
            "answer": "Passed.",
            "error": None,
            "note": None,
            "finished_at": time.time(),
        }
    ]

    line = host.follow_up_context()

    assert "Check CI run 4012" in line and "Passed." in line
    assert host.follow_up_context() == ""
    assert all(c["start"] is False for c in calls if c["method"] == "GET")


def test_an_agent_without_the_tools_never_asks_the_daemon(calls):
    assert _Host().follow_up_context() == ""
    assert calls == []


def test_no_running_daemon_leaves_the_turn_without_the_line(monkeypatch):
    def none_running(*a, **k):
        raise DaemonError("no GAIA daemon is running, so no follow-up can have run.")

    monkeypatch.setattr(client, "followups_request", none_running)
    host = _Host()
    host._follow_ups_enabled = True
    assert host.follow_up_context() == ""
