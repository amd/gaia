# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""The relay's idle-read budget, pinned to a table the TUI also reads.

The TUI computes the same budget in Go (tui/internal/client/sse.go) so it gives
up on a relayed turn no earlier than the relay does. Both sides read
tests/fixtures/daemon/agent_read_timeout_cases.json, so a change to either
parser that the other does not share fails a test.
"""

import json
import math
from pathlib import Path

import pytest

from gaia.daemon.timeouts import agent_read_timeout

TABLE = (
    Path(__file__).resolve().parents[1]
    / "fixtures"
    / "daemon"
    / "agent_read_timeout_cases.json"
)
CASES = json.loads(TABLE.read_text(encoding="utf-8"))["cases"]


@pytest.mark.parametrize("case", CASES, ids=[repr(c["value"]) for c in CASES])
def test_agent_read_timeout_matches_the_shared_table(monkeypatch, case):
    monkeypatch.setenv("GAIA_AGENT_TOOL_TIMEOUT", case["value"])
    if case.get("error"):
        with pytest.raises(ValueError):
            agent_read_timeout()
        return
    got = agent_read_timeout()
    if case["seconds"] == "inf":
        assert math.isinf(got)
    else:
        assert got == case["seconds"]


def test_the_daemon_status_advertises_its_relay_budget(monkeypatch):
    from fastapi.testclient import TestClient

    from gaia.daemon.app import create_app

    monkeypatch.setenv("GAIA_AGENT_TOOL_TIMEOUT", "600")
    app = create_app(token="tok-budget", port=1234, pid=111, started_at=0.0)
    r = TestClient(app).get(
        "/daemon/v1/status", headers={"Authorization": "Bearer tok-budget"}
    )

    assert r.status_code == 200
    assert r.json()["agent_read_timeout_s"] == 660.0


def test_a_bad_relay_budget_is_reported_without_breaking_status(monkeypatch):
    from fastapi.testclient import TestClient

    from gaia.daemon.app import create_app

    monkeypatch.setenv("GAIA_AGENT_TOOL_TIMEOUT", "soon")
    app = create_app(token="tok-budget", port=1234, pid=111, started_at=0.0)
    r = TestClient(app).get(
        "/daemon/v1/status", headers={"Authorization": "Bearer tok-budget"}
    )

    assert r.status_code == 200
    body = r.json()
    assert "agent_read_timeout_s" not in body
    assert "GAIA_AGENT_TOOL_TIMEOUT" in body["agent_read_timeout_error"]


def test_an_unlimited_relay_budget_is_sent_as_null(monkeypatch):
    from fastapi.testclient import TestClient

    from gaia.daemon.app import create_app

    monkeypatch.setenv("GAIA_AGENT_TOOL_TIMEOUT", "inf")
    app = create_app(token="tok-budget", port=1234, pid=111, started_at=0.0)
    r = TestClient(app).get(
        "/daemon/v1/status", headers={"Authorization": "Bearer tok-budget"}
    )

    assert r.status_code == 200
    assert r.json()["agent_read_timeout_s"] is None
