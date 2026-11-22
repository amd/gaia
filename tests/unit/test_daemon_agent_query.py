# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Unit tests for the daemon thin-client query timeout contract."""

from types import SimpleNamespace

import pytest
import requests

from gaia.daemon import agent_query
from gaia.daemon.errors import DaemonError


class _Response:
    status_code = 200

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False


def test_run_query_passes_configured_read_timeout(monkeypatch):
    observed = {}

    monkeypatch.setattr(
        agent_query.client,
        "ensure_agent",
        lambda _agent_id: SimpleNamespace(
            base_url="http://127.0.0.1:12345", token="daemon-token"
        ),
    )

    def _post(*_args, **kwargs):
        observed["timeout"] = kwargs["timeout"]
        return _Response()

    monkeypatch.setattr(requests, "post", _post)
    monkeypatch.setattr(agent_query, "_consume", lambda *_args: "final")
    monkeypatch.setenv("GAIA_AGENT_TOOL_TIMEOUT", "900")

    renderer = SimpleNamespace(
        final_answer=None,
        final_failed=False,
        final_usage=None,
        final_model_messages=None,
        final_time_limit_reached=False,
        final_withheld_tools=None,
        error_detail=None,
    )
    outcome = agent_query.run_query("email", "triage", renderer=renderer)

    assert outcome.exit_code == 0
    assert observed["timeout"] == (10.0, 960.0)


class _Version:
    def __init__(self, api_version):
        self._api_version = api_version

    def raise_for_status(self):
        pass

    def json(self):
        return {"apiVersion": self._api_version}


def _stub_daemon(monkeypatch, api_version):
    sent = []
    monkeypatch.setattr(
        agent_query.client,
        "ensure_agent",
        lambda _agent_id: SimpleNamespace(base_url="http://127.0.0.1:1", token="t"),
    )
    monkeypatch.setattr(requests, "get", lambda *a, **k: _Version(api_version))

    def _post(*_args, **kwargs):
        sent.append(kwargs["json"])
        return _Response()

    monkeypatch.setattr(requests, "post", _post)
    monkeypatch.setattr(agent_query, "_consume", lambda *_args: "final")
    return sent


def test_run_scoped_flags_reach_the_request_on_a_new_enough_agent(monkeypatch):
    sent = _stub_daemon(monkeypatch, "2.31")
    agent_query.run_query(
        "gaia",
        "q",
        time_limit=5,
        disallowed_tools=["write_file"],
        no_web=True,
        no_skills=True,
        skip_permissions=True,
        full_access=True,
    )
    [payload] = sent
    assert {k: payload[k] for k in agent_query.RUN_SCOPED_FLAGS} == {
        "time_limit": 5,
        "disallowed_tools": ["write_file"],
        "no_web": True,
        "no_skills": True,
        "skip_permissions": True,
        "full_access": True,
    }


def test_an_agent_older_than_the_flags_is_refused_not_run_unrestricted(monkeypatch):
    sent = _stub_daemon(monkeypatch, "2.27")
    with pytest.raises(DaemonError, match="--disallowed-tools"):
        agent_query.run_query("gaia", "q", disallowed_tools=["write_file"])
    assert sent == []


def test_no_run_scoped_flag_means_no_version_check(monkeypatch):
    sent = _stub_daemon(monkeypatch, "2.27")
    agent_query.run_query("gaia", "q")
    [payload] = sent
    assert not set(payload) & set(agent_query.RUN_SCOPED_FLAGS)


def test_run_scoped_flags_are_refused_with_a_session(monkeypatch):
    sent = _stub_daemon(monkeypatch, "2.31")
    with pytest.raises(ValueError, match="one run only"):
        agent_query.run_query("gaia", "q", session_id="chat-1", full_access=True)
    assert sent == []
