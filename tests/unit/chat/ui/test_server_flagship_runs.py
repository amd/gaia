# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Who starts the model server, and which agent unattended runs use.

- ``start_model_server_owner`` brings up the daemon (which owns Lemonade) off
  the main thread. Only the runners that serve the UI call it; ``create_app``
  is a library factory and must not.
- Scheduled runs build the flagship ``GaiaAgent``, not ``ChatAgent``.
"""

import asyncio
import os
import sys
import threading
import types
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from gaia.llm import lemonade_service
from gaia.ui import _chat_helpers as helpers
from gaia.ui import server

pytestmark = pytest.mark.allow_network


def test_start_model_server_owner_runs_the_daemon_start_in_a_daemon_thread(
    monkeypatch,
):
    called = threading.Event()
    seen = {}

    def ensure():
        seen["thread"] = threading.current_thread()
        called.set()

    monkeypatch.setattr(lemonade_service, "ensure_daemon_owns_lemonade", ensure)
    thread = server.start_model_server_owner()
    assert called.wait(2)
    thread.join(2)
    assert thread.daemon
    assert thread.name == "gaia-daemon-start"
    assert seen["thread"] is thread
    assert seen["thread"] is not threading.main_thread()


def test_create_app_never_starts_the_daemon(monkeypatch):
    def refuse(*_a, **_k):
        raise AssertionError("create_app must not start the daemon")

    monkeypatch.setattr(lemonade_service, "ensure_daemon_owns_lemonade", refuse)
    monkeypatch.setattr(server, "start_model_server_owner", refuse)
    # The app's startup sets a process-wide registry; keep it out of later tests.
    monkeypatch.setattr(helpers, "_agent_registry", None)
    with TestClient(server.create_app(db_path=":memory:")) as client:
        assert client.get("/api/health").status_code in (200, 503)


@pytest.mark.parametrize(
    "base_url,env_url,expected",
    [
        (None, None, 1),
        ("http://10.0.0.5:8000", None, 0),
        (None, "http://10.0.0.5:8000", 0),
        (None, "http://127.0.0.1:8000/api/v1", 1),
    ],
)
def test_launch_agent_ui_starts_the_owner_only_for_a_local_server(
    monkeypatch, base_url, env_url, expected
):
    import gaia.cli as gaia_cli

    if env_url is None:
        monkeypatch.delenv("LEMONADE_BASE_URL", raising=False)
    else:
        monkeypatch.setenv("LEMONADE_BASE_URL", env_url)
    # _launch_agent_ui exports --base-url into os.environ; don't leak it.
    with (
        patch.dict(os.environ),
        patch.object(gaia_cli, "_ensure_webui_built"),
        patch("gaia.ui.server.create_app"),
        patch("gaia.ui.server.start_model_server_owner") as owner,
        patch("uvicorn.run"),
    ):
        gaia_cli._launch_agent_ui(port=0, base_url=base_url, log=MagicMock())
    assert owner.call_count == expected


@pytest.mark.parametrize(
    "base_url,expected",
    [
        (None, 1),
        ("http://127.0.0.1:8000/api/v1", 1),
        ("http://localhost:8000", 1),
        ("http://10.0.0.5:8000", 0),
    ],
)
def test_standalone_runner_starts_the_owner_only_for_a_local_server(
    monkeypatch, base_url, expected
):
    if base_url is None:
        monkeypatch.delenv("LEMONADE_BASE_URL", raising=False)
    else:
        monkeypatch.setenv("LEMONADE_BASE_URL", base_url)
    monkeypatch.setattr(sys, "argv", ["gaia-ui"])
    with (
        patch("gaia.ui.server.create_app"),
        patch("gaia.ui.server.start_model_server_owner") as owner,
        patch("uvicorn.run"),
    ):
        server.main()
    assert owner.call_count == expected


def test_scheduled_runs_use_the_flagship(monkeypatch):
    built = []

    class FakeConfig:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    class FakeGaiaAgent:
        def __init__(self, config):
            built.append(config)

        def process_query(self, prompt):
            return {"result": f"did {prompt}"}

    fake = types.ModuleType("gaia_agent.agent")
    fake.GaiaAgent = FakeGaiaAgent
    fake.GaiaAgentConfig = FakeConfig
    monkeypatch.setitem(sys.modules, "gaia_agent.agent", fake)
    chat_mod = types.ModuleType("gaia_agent_chat.agent")

    def _chat_agent(*_a, **_k):
        raise AssertionError("scheduled runs must not build ChatAgent")

    chat_mod.ChatAgent = chat_mod.ChatAgentConfig = _chat_agent
    monkeypatch.setitem(sys.modules, "gaia_agent_chat.agent", chat_mod)
    monkeypatch.setattr(lemonade_service, "ensure_daemon_owns_lemonade", lambda: None)

    with TestClient(server.create_app(db_path=":memory:")) as client:
        executor = client.app.state.scheduler._executor
        assert asyncio.run(executor("summarize")) == "did summarize"

    assert len(built) == 1
    assert built[0].kwargs["max_steps"] == 5
    assert built[0].kwargs["silent_mode"] is True
