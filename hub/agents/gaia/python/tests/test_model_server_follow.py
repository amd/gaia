# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""A model server chosen in Settings reaches a chat that is already open.

An agent's model client takes its server address as it is built, so a chat
open when the user moves to another Lemonade Server would keep asking the old
one. A real flagship runs here against two stand-in servers, each recording
what it is asked.
"""

from __future__ import annotations

import json

import pytest

from gaia import config as config_mod
from gaia.llm.lemonade_client import DEFAULT_MODEL_NAME
from gaia.llm.lemonade_manager import LemonadeManager
from tests.fixtures.fake_lemonade import FakeLemonade

MODELS = (DEFAULT_MODEL_NAME, "user.embeddinggemma-300m-GGUF")


@pytest.fixture
def servers(tmp_path, monkeypatch):
    """Two Lemonade Servers the user could choose; ``choose(n)`` saves one."""
    gaia_dir = tmp_path / "home"
    gaia_dir.mkdir()
    (tmp_path / "work").mkdir()
    monkeypatch.chdir(tmp_path / "work")
    monkeypatch.setenv("GAIA_HOME", str(gaia_dir))
    monkeypatch.setenv("GAIA_MEMORY_DB", str(gaia_dir / "memory.db"))
    monkeypatch.setenv("GAIA_DAEMON_HOME", str(tmp_path / "daemon"))
    monkeypatch.setenv("GAIA_PROJECT_MAP_AUTO_INDEX", "0")
    monkeypatch.delenv("LEMONADE_BASE_URL", raising=False)
    monkeypatch.delenv("GAIA_CTX_SIZE", raising=False)
    config_file = gaia_dir / "config.json"
    monkeypatch.setattr(config_mod, "GAIA_CONFIG_FILE", config_file)
    LemonadeManager.reset()
    with (
        FakeLemonade(downloaded=MODELS) as first,
        FakeLemonade(downloaded=MODELS) as second,
    ):
        pair = (first, second)

        def choose(index: int) -> None:
            config_file.write_text(
                json.dumps(
                    {
                        "lemonade_mode": "external",
                        "lemonade_base_url": pair[index].base_url,
                    }
                ),
                encoding="utf-8",
            )

        yield pair, choose, str(tmp_path / "work")
    LemonadeManager.reset()


def _chats(server, since):
    return [r for r in server.requests[since:] if r.path.endswith("/chat/completions")]


def test_an_open_chat_asks_the_server_chosen_since(servers):
    from gaiapy import session_registry as sr

    (first, second), choose, work = servers
    choose(0)
    registry = sr._SessionRegistry()
    kwargs = {"allowed_paths": [work]}
    try:
        session, _ = registry.checkout("chat-1", **kwargs)
        session.agent.process_query("hello")
        session.run_lock.release()
        assert _chats(first, 0)

        choose(1)  # Settings › Advanced › Model server
        before = (len(first.requests), len(second.requests))
        session, _ = registry.checkout("chat-1", **kwargs)
        assert session.follow_settings(**kwargs) is True
        session.agent.process_query("and again")
        session.run_lock.release()
        assert _chats(second, before[1]), "the new server was never asked"
        assert not _chats(first, before[0]), "the old server was still asked"
    finally:
        registry.clear()


def test_an_unchanged_server_keeps_the_agent(servers):
    from gaiapy import session_registry as sr

    _, choose, work = servers
    choose(0)
    registry = sr._SessionRegistry()
    kwargs = {"allowed_paths": [work]}
    try:
        session, _ = registry.checkout("chat-1", **kwargs)
        agent = session.agent
        assert session.follow_settings(**kwargs) is False
        assert session.agent is agent
        session.run_lock.release()
    finally:
        registry.clear()


@pytest.mark.parametrize(
    "built, now, changed",
    [
        (None, "http://192.168.1.20:13305/api/v1", True),  # GAIA's own -> yours
        ("http://192.168.1.20:13305/api/v1", None, True),  # yours -> GAIA's own
        (None, None, False),  # GAIA's own, wherever it restarted
        ("http://a:1/api/v1", "http://a:1/api/v1", False),
    ],
)
def test_switching_to_or_from_gaias_own_server_is_a_change(
    monkeypatch, built, now, changed
):
    from gaiapy import chat_agent
    from gaiapy.chat_agent import ChatAgent

    agent = ChatAgent.__new__(ChatAgent)
    agent._model_server_at_build = built
    monkeypatch.setattr(chat_agent, "configured_lemonade_url", lambda: now)
    assert agent.model_server_changed() is changed
