# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""An existing chat follows GAIA's Lemonade Server when the daemon restarts it.

GAIA's embedded server binds a new port and mints a new API key on every start.
A chat agent is cached per session (and the flagship sidecar lives across
turns), so its client was built against the old server. Before this, every turn
in that chat failed with "Couldn't reach the local LLM" until the user opened a
new one.

Mocks sit at the HTTP boundary (``requests.post``) and the daemon boundary
(``ensure_lemonade``); the state file is the real one GAIA reads.
"""

import json

import pytest

from gaia.llm import lemonade_client as lc
from gaia.llm.lemonade_embedded import EmbeddedLemonade
from gaia.llm.lemonade_manager import LemonadeManager
from gaia.llm.providers.lemonade import LemonadeProvider

OLD_PORT, OLD_KEY = 41001, "key-before-restart"
NEW_PORT, NEW_KEY = 41002, "key-after-restart"
MODEL = "Gemma-4-E4B-it-GGUF"


class _Response:
    status_code = 200

    def json(self):
        return {"choices": [{"message": {"content": "hi"}, "finish_reason": "stop"}]}


class Server:
    """GAIA's embedded server as its state file and HTTP surface show it."""

    def __init__(self, embedded: EmbeddedLemonade, monkeypatch):
        self.embedded = embedded
        self.live_pids = {1}
        self.requests = []
        self.daemon_calls = 0
        monkeypatch.setattr(
            "gaia.llm.lemonade_embedded.pid_exists", lambda pid: pid in self.live_pids
        )

    def record(self, pid: int, port: int, key: str) -> None:
        self.embedded.root.mkdir(parents=True, exist_ok=True)
        self.embedded.state_path.write_text(
            json.dumps({"pid": pid, "port": port, "api_key": key, "version": "v"})
        )

    def post(self, url, json=None, headers=None, timeout=None):
        self.requests.append((url, (headers or {}).get("Authorization")))
        return _Response()


@pytest.fixture
def server(tmp_path, monkeypatch):
    monkeypatch.setenv("GAIA_HOME", str(tmp_path / "gaia-home"))
    for var in ("LEMONADE_BASE_URL", "LEMONADE_API_KEY", "GAIA_LEMONADE_EMBEDDED"):
        monkeypatch.delenv(var, raising=False)
    s = Server(EmbeddedLemonade(), monkeypatch)
    s.record(pid=1, port=OLD_PORT, key=OLD_KEY)
    monkeypatch.setattr(lc.requests, "post", s.post)
    monkeypatch.setattr(lc.LemonadeClient, "_ensure_model_loaded", lambda *a, **k: None)
    LemonadeManager.reset()
    yield s
    LemonadeManager.reset()


def _chat_session():
    """The client a cached agent holds: built once, with the URL it resolved then."""
    return LemonadeProvider(model=MODEL, base_url=lc.resolve_lemonade_base_url())


def _last_request(server):
    return server.requests[-1]


def test_next_turn_uses_the_port_and_key_the_daemon_restarted_on(server):
    chat = _chat_session()
    chat.chat([{"role": "user", "content": "first"}])
    assert _last_request(server) == (
        f"http://localhost:{OLD_PORT}/api/v1/chat/completions",
        f"Bearer {OLD_KEY}",
    )

    # Lemonade died; the daemon brought up a replacement elsewhere.
    server.live_pids = {2}
    server.record(pid=2, port=NEW_PORT, key=NEW_KEY)

    chat.chat([{"role": "user", "content": "second"}])
    assert _last_request(server) == (
        f"http://localhost:{NEW_PORT}/api/v1/chat/completions",
        f"Bearer {NEW_KEY}",
    )


def test_a_dead_server_is_replaced_through_the_daemon(server, monkeypatch):
    chat = _chat_session()

    def daemon_restarts(*args, **kwargs):
        server.daemon_calls += 1
        server.live_pids = {2}
        server.record(pid=2, port=NEW_PORT, key=NEW_KEY)
        return {"base_url": f"http://localhost:{NEW_PORT}/api/v1", "started": True}

    monkeypatch.setattr("gaia.daemon.client.ensure_lemonade", daemon_restarts)
    server.live_pids = set()  # killed; nothing has restarted it yet

    chat.chat([{"role": "user", "content": "after the crash"}])

    assert server.daemon_calls == 1
    assert _last_request(server) == (
        f"http://localhost:{NEW_PORT}/api/v1/chat/completions",
        f"Bearer {NEW_KEY}",
    )


def test_streaming_turns_follow_too(server, monkeypatch):
    chat = _chat_session()
    seen = []

    def stream_chunks(self, **kwargs):
        seen.append((self.base_url, self.api_key))
        yield {"choices": [{"delta": {"content": "hi"}, "finish_reason": "stop"}]}

    monkeypatch.setattr(lc.LemonadeClient, "_stream_chat_chunks", stream_chunks)
    server.live_pids = {2}
    server.record(pid=2, port=NEW_PORT, key=NEW_KEY)

    "".join(chat.chat([{"role": "user", "content": "hi"}], stream=True))

    assert seen == [(f"http://localhost:{NEW_PORT}/api/v1", NEW_KEY)]


def test_genuinely_down_stays_an_actionable_error(server, monkeypatch):
    from gaia.daemon.errors import DaemonError

    def daemon_cannot(*args, **kwargs):
        raise DaemonError("lemond exited during start; read lemond.log")

    monkeypatch.setattr("gaia.daemon.client.ensure_lemonade", daemon_cannot)
    chat = _chat_session()
    server.live_pids = set()

    with pytest.raises(lc.LemonadeClientError, match="read lemond.log"):
        chat.chat([{"role": "user", "content": "hi"}])
    assert server.requests == []


def test_a_server_the_user_configured_is_never_redirected(server, monkeypatch):
    monkeypatch.setenv("LEMONADE_BASE_URL", f"http://localhost:{OLD_PORT}")
    monkeypatch.setenv("LEMONADE_API_KEY", "user-key")
    chat = _chat_session()
    server.live_pids = {2}
    server.record(pid=2, port=NEW_PORT, key=NEW_KEY)

    chat.chat([{"role": "user", "content": "hi"}])

    assert _last_request(server) == (
        f"http://localhost:{OLD_PORT}/api/v1/chat/completions",
        "Bearer user-key",
    )


def _serve_status(monkeypatch):
    monkeypatch.setattr(
        lc.LemonadeClient,
        "get_status",
        lambda self: lc.LemonadeStatus(
            url=self.base_url,
            running=self.base_url == lc.running_embedded_lemonade_url(),
            context_size=65536,
            loaded_models=[{"id": MODEL, "type": "llm"}],
        ),
    )
    monkeypatch.setattr(
        lc.LemonadeClient, "get_model_max_context_window", lambda *a, **k: None
    )


def test_manager_base_url_follows_the_restart(server, monkeypatch):
    _serve_status(monkeypatch)
    monkeypatch.setattr(
        "gaia.llm.lemonade_manager.gaia_runs_lemonade", lambda *a: False
    )
    assert LemonadeManager.ensure_ready(min_context_size=65536, model=MODEL)
    assert LemonadeManager.get_base_url() == f"http://localhost:{OLD_PORT}/api/v1"

    server.live_pids = {2}
    server.record(pid=2, port=NEW_PORT, key=NEW_KEY)

    assert LemonadeManager.get_base_url() == f"http://localhost:{NEW_PORT}/api/v1"


def test_a_new_chat_after_a_crash_has_the_daemon_start_the_server(server, monkeypatch):
    """A warm manager must not keep vouching for a server that has exited."""
    _serve_status(monkeypatch)
    monkeypatch.setattr("gaia.llm.lemonade_manager.gaia_runs_lemonade", lambda *a: True)

    def start_if_stopped():
        if lc.running_embedded_lemonade_url() is not None:
            return False
        server.daemon_calls += 1
        server.live_pids = {2}
        server.record(pid=2, port=NEW_PORT, key=NEW_KEY)
        return True

    monkeypatch.setattr(
        LemonadeManager, "start_embedded_if_stopped", staticmethod(start_if_stopped)
    )
    assert LemonadeManager.ensure_ready(min_context_size=65536, model=MODEL)
    assert server.daemon_calls == 0

    server.live_pids = set()
    stopped_default = lc.resolve_lemonade_base_url()  # what a new agent resolves

    assert LemonadeManager.ensure_ready(
        min_context_size=65536, model=MODEL, base_url=stopped_default
    )
    assert server.daemon_calls == 1
    assert LemonadeManager.get_base_url() == f"http://localhost:{NEW_PORT}/api/v1"


def _install_embedded(server):
    server.embedded.daemon_path.parent.mkdir(parents=True, exist_ok=True)
    server.embedded.daemon_path.write_text("")


def test_a_client_built_while_the_server_was_down_finds_it(server):
    _install_embedded(server)
    server.live_pids = set()
    chat = _chat_session()
    assert chat._backend.base_url == "http://localhost:13305/api/v1"

    # Another caller had the daemon start it in the meantime.
    server.live_pids = {2}
    server.record(pid=2, port=NEW_PORT, key=NEW_KEY)
    chat.chat([{"role": "user", "content": "hi"}])

    assert _last_request(server) == (
        f"http://localhost:{NEW_PORT}/api/v1/chat/completions",
        f"Bearer {NEW_KEY}",
    )


def test_a_new_chat_agent_waits_for_the_port_the_server_starts_on(server, monkeypatch):
    """The chat agent used to pin the URL it resolved while the server was down."""
    from unittest.mock import patch

    from gaia_agent_chat.agent import ChatAgent, ChatAgentConfig

    _install_embedded(server)
    server.live_pids = set()
    monkeypatch.setenv("GAIA_MEMORY_DISABLED", "1")

    def daemon_starts_it(**kwargs):
        server.live_pids = {2}
        server.record(pid=2, port=NEW_PORT, key=NEW_KEY)
        return True

    with (
        patch(
            "gaia.llm.lemonade_manager.LemonadeManager.ensure_ready",
            side_effect=daemon_starts_it,
        ),
        patch("gaia.agents.base.agent.AgentSDK") as sdk,
        patch("gaia.rag.sdk.AgentSDK"),
    ):
        agent = ChatAgent(ChatAgentConfig(silent_mode=True, max_steps=1))
        agent.stop_watching()

    assert sdk.call_args.args[0].base_url == f"http://localhost:{NEW_PORT}/api/v1"


def test_a_system_lemonade_on_the_default_port_is_left_alone(server):
    server.live_pids = set()  # GAIA's own server is not installed in this home
    chat = _chat_session()

    chat.chat([{"role": "user", "content": "hi"}])

    assert _last_request(server) == (
        "http://localhost:13305/api/v1/chat/completions",
        None,
    )
