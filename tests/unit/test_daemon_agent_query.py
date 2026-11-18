# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Unit tests for the daemon thin-client query timeout contract."""

from types import SimpleNamespace

import pytest
import requests

from gaia.daemon import agent_query


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
        error_detail=None,
    )
    outcome = agent_query.run_query("email", "triage", renderer=renderer)

    assert outcome.exit_code == 0
    assert observed["timeout"] == (10.0, 960.0)


def _renderer():
    return SimpleNamespace(
        final_answer=None,
        final_failed=False,
        final_usage=None,
        final_model_messages=None,
        error_detail=None,
    )


def test_run_query_sends_an_infinite_timeout_as_no_limit(monkeypatch):
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
    monkeypatch.setenv("GAIA_AGENT_TOOL_TIMEOUT", "inf")

    outcome = agent_query.run_query("email", "triage", renderer=_renderer())

    assert outcome.exit_code == 0
    assert observed["timeout"] == (10.0, None)


def test_run_query_rejects_a_malformed_timeout_before_anything_starts(monkeypatch):
    from gaia.daemon.errors import DaemonError

    started = []
    monkeypatch.setattr(
        agent_query.client, "ensure_agent", lambda agent_id: started.append(agent_id)
    )
    monkeypatch.setattr(requests, "post", lambda *a, **k: started.append("post"))
    monkeypatch.setenv("GAIA_AGENT_TOOL_TIMEOUT", "soon")

    with pytest.raises(DaemonError, match="GAIA_AGENT_TOOL_TIMEOUT"):
        agent_query.run_query("email", "triage", renderer=_renderer())
    assert started == []
