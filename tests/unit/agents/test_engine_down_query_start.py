# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""A cloud chat sent while GAIA's AI engine can't start says so, and how to fix it.

With GAIA's own ``lemond`` missing, a DeepSeek-on-Fireworks chat in the Agent
UI showed "Failed to start the query run: Request failed: HTTPConnectionPool(
host='localhost', port=13305) …" and told the user to run a terminal command.
Building the agent probed the default port instead of asking the daemon for
GAIA's engine, and the sidecar returned the raw exception.
"""

import time
import uuid
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
import requests

from gaia.daemon.errors import LemonadeStartFailedError
from gaia.llm import lemonade_client as lc
from gaia.llm.lemonade_embedded import EmbeddedLemonade
from gaia.llm.surface_hints import AGENT_UI_SETUP_HINT, TUI_SETUP_HINT

pytest.importorskip("gaia_agent")

from fastapi.testclient import TestClient  # noqa: E402
from gaia_agent import caller_auth  # noqa: E402
from gaia_agent import server as server_mod  # noqa: E402

_REASON = "GAIA's Lemonade Server install is incomplete: lemond.exe is missing"
_HEADLINE = "GAIA's AI engine couldn't start"
_RAW = ("HTTPConnectionPool", "Max retries", "WinError", "Request failed")
_COMMANDS = ("gaia lemonade", "gaia init", "gaia daemon", "run:", "Run:")


@pytest.fixture
def engine_cannot_start(tmp_path, monkeypatch, gaia_owned_lemonade):
    """GAIA's engine is unpacked without ``lemond``; the daemon can't start it."""
    monkeypatch.setenv("GAIA_HOME", str(tmp_path))
    # Memory's warm-up asks the daemon too; this counts the chat's own asks.
    monkeypatch.setenv("GAIA_MEMORY_DISABLED", "1")
    EmbeddedLemonade().dist_dir.mkdir(parents=True)
    ensure = MagicMock(
        side_effect=LemonadeStartFailedError("start failed", reason=_REASON)
    )
    monkeypatch.setattr("gaia.daemon.client.ensure_lemonade", ensure)
    sent: list = []
    real_get = requests.get

    def recording_get(url, *args, **kwargs):
        sent.append(url)
        return real_get(url, *args, **kwargs)

    monkeypatch.setattr(lc.requests, "get", recording_get)
    return ensure, sent


def _post_query(surface: str = "agent_ui"):
    caller_auth.reset()
    try:
        client = TestClient(server_mod.build_app(), base_url="http://127.0.0.1:8141")
        return client.post(
            "/v1/gaia/query",
            json={
                "query": "hello, are you there?",
                "run_id": str(uuid.uuid4()),
                "context": [],
                "session_id": "engine-down",
                "model": "fireworks.deepseek-v4p1-flash",
                "surface": surface,
            },
        )
    finally:
        caller_auth.reset()


class TestACloudChatWithTheEngineDown:
    def test_the_chat_names_the_engine_and_the_setup_fix(self, engine_cannot_start):
        ensure, sent = engine_cannot_start
        started = time.monotonic()
        response = _post_query()
        elapsed = time.monotonic() - started

        assert response.status_code == 503, response.text
        detail = response.json()["detail"]
        assert detail.startswith(_HEADLINE), detail
        assert _REASON in detail
        assert AGENT_UI_SETUP_HINT in detail
        assert TUI_SETUP_HINT not in detail
        assert not any(raw in detail for raw in _RAW), detail
        assert not any(command in detail for command in _COMMANDS), detail
        assert not [url for url in sent if ":13305" in url], sent
        # One failed start is final: no reconnect wait, no second start.
        assert ensure.call_count == 1
        assert elapsed < 20

    def test_a_tui_caller_gets_the_tuis_own_fix(self, engine_cannot_start):
        detail = _post_query(surface="tui").json()["detail"]
        assert TUI_SETUP_HINT in detail
        assert AGENT_UI_SETUP_HINT not in detail


def test_an_unrelated_failure_is_still_reported_as_it_happened(monkeypatch):
    def broken(*_args, **_kwargs):
        raise RuntimeError("scripted build failure")

    monkeypatch.setattr(server_mod, "build_query_agent", broken)
    monkeypatch.setattr(server_mod, "_checkout_turn", broken)
    response = _post_query()
    assert response.status_code == 500
    assert "scripted build failure" in response.json()["detail"]


def test_a_failed_start_is_not_waited_out_again():
    client = lc.LemonadeClient.__new__(lc.LemonadeClient)
    client.follows_gaia_lemonade = True
    client._gaia_lemonade_gone = lambda: True
    client._gone_handoff = lambda: SimpleNamespace(pending=True)
    client.wait_for_gaia_lemonade = MagicMock()
    client.log = MagicMock()
    failed = lc.GaiaLemonadeStartError("start failed", reason=_REASON, log_file=None)
    assert client._recover_stopped_gaia_lemonade(failed, "model") is False
    client.wait_for_gaia_lemonade.assert_not_called()
