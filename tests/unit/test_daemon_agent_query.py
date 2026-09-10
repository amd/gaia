# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Unit tests for the daemon thin-client query timeout contract."""

from types import SimpleNamespace

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

    renderer = SimpleNamespace(final_answer=None, error_detail=None)
    outcome = agent_query.run_query("email", "triage", renderer=renderer)

    assert outcome.exit_code == 0
    assert observed["timeout"] == (10.0, 960.0)
