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


def _result(job_id, task, answer="Passed."):
    return {
        "job_id": job_id,
        "task": task,
        "answer": answer,
        "error": None,
        "note": None,
        "finished_at": time.time(),
    }


def _enabled(host=None):
    host = host or _Host()
    host._follow_ups_enabled = True
    return host


class _Config:
    def __init__(self, session):
        self.ui_session_id = session


def test_the_turn_reports_finished_follow_ups_once(calls):
    host = _enabled()
    calls.state["results"] = [_result("j1", "Check CI run 4012")]

    line = host.follow_up_context()
    host.follow_ups_told(answered=True)

    assert "Check CI run 4012" in line and "Passed." in line
    assert host.follow_up_context() == ""
    assert all(c["start"] is False for c in calls)


def test_a_turn_that_failed_shows_the_result_again(calls):
    host = _enabled()
    calls.state["results"] = [_result("j1", "Check CI run 4012")]

    host.follow_up_context()
    host.follow_ups_told(answered=False)

    assert "Check CI run 4012" in host.follow_up_context()
    assert not [c for c in calls if c["path"] == "/seen"]


def test_a_fired_run_never_takes_another_follow_ups_result(calls):
    """A fires, B fires, the user chats: B's own run must not use up A's result."""
    from gaia.daemon import followups

    fired = _enabled()
    fired.config = _Config(followups.run_session_id("b"))
    calls.state["results"] = [_result("a", "Check build A")]

    assert fired.follow_up_context() == ""
    fired._list_follow_ups()
    fired.follow_ups_told(answered=True)
    assert not [c for c in calls if c["path"] == "/seen"]

    calls.state["results"].append(_result("b", "Check build B"))
    user = _enabled()
    line = user.follow_up_context()
    assert "Check build A" in line and "Check build B" in line


def test_listing_mid_turn_adds_to_what_the_turn_showed(calls):
    host = _enabled()
    calls.state["results"] = [_result("j1", "first")]
    host.follow_up_context()
    calls.state["results"] = [_result("j1", "first"), _result("j2", "second")]

    host._list_follow_ups()
    host.follow_ups_told(answered=True)

    [seen] = [c for c in calls if c["path"] == "/seen"]
    assert seen["json"]["ids"] == ["j1", "j2"]


def test_marking_seen_without_a_daemon_never_fails_the_turn(calls, monkeypatch):
    host = _enabled()
    calls.state["results"] = [_result("j1", "x")]
    host.follow_up_context()

    def gone(*a, **k):
        raise DaemonError("no GAIA daemon is running")

    monkeypatch.setattr(client, "followups_request", gone)
    host.follow_ups_told(answered=True)


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
