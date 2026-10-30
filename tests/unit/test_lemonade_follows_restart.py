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


def test_current_base_url_follows_the_restart(server, monkeypatch):
    """A caller that builds its own client from it (a model switch) must not
    get the dead port."""
    chat = _chat_session()
    assert chat.current_base_url() == f"http://localhost:{OLD_PORT}/api/v1"

    server.live_pids = {2}
    server.record(pid=2, port=NEW_PORT, key=NEW_KEY)

    assert chat.current_base_url() == f"http://localhost:{NEW_PORT}/api/v1"
    monkeypatch.setenv("LEMONADE_BASE_URL", f"http://localhost:{OLD_PORT}")
    assert _chat_session().current_base_url() == f"http://localhost:{OLD_PORT}/api/v1"


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
    pytest.importorskip("gaia_agent_chat")
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


CLOUD_MODEL = "amd.DeepSeek-V4.1-Flash"


class _JSONResponse:
    status_code = 200
    content = b"{}"

    def __init__(self, body):
        self._body = body

    def json(self):
        return self._body


class _Gateway:
    """Lemonade's cloud-provider API: it knows the AMD provider, not its key."""

    def __init__(self, server, monkeypatch):
        self.server = server
        self.calls = []
        self.holds_key = False
        monkeypatch.setattr("gaia.llm.gateway.recall_token", lambda: "kept-key")
        monkeypatch.setattr("gaia.llm.gateway.requests.request", self.request)

    def request(self, method, url, json=None, headers=None, timeout=None, **_):
        auth = (headers or {}).get("Authorization")
        self.calls.append((method, url, auth))
        # Shares the chat request log, so the replay-then-turn order is checked.
        self.server.requests.append((url, auth))
        if url.endswith("/system-info"):
            entry = {
                "name": "amd",
                "runtime_key_set": self.holds_key,
                "models_discovered": 3 if self.holds_key else 0,
            }
            return _JSONResponse({"cloud": {"providers": [entry]}})
        assert json == {"provider": "amd", "api_key": "kept-key"}
        self.holds_key = True
        return _JSONResponse({"models_discovered": 3})


def _cloud_session():
    return LemonadeProvider(model=CLOUD_MODEL, base_url=lc.resolve_lemonade_base_url())


def test_a_restarted_server_gets_the_cloud_key_back_before_the_turn(
    server, monkeypatch
):
    """Lemonade forgets cloud keys on restart; every cloud turn 404ed after one."""
    gateway = _Gateway(server, monkeypatch)
    chat = _cloud_session()
    chat.chat([{"role": "user", "content": "first"}])
    assert gateway.calls == []  # nothing moved, so no per-turn cost

    server.live_pids = {2}
    server.record(pid=2, port=NEW_PORT, key=NEW_KEY)
    chat.chat([{"role": "user", "content": "second"}])

    new = f"http://localhost:{NEW_PORT}/api/v1"
    assert gateway.calls == [
        ("GET", f"{new}/system-info", f"Bearer {NEW_KEY}"),
        ("POST", f"{new}/cloud/auth", f"Bearer {NEW_KEY}"),
    ]
    assert _last_request(server) == (f"{new}/chat/completions", f"Bearer {NEW_KEY}")


def test_a_server_the_daemon_restarts_mid_chat_gets_the_cloud_key_back(
    server, monkeypatch
):
    gateway = _Gateway(server, monkeypatch)

    def daemon_restarts(*args, **kwargs):
        server.live_pids = {2}
        server.record(pid=2, port=NEW_PORT, key=NEW_KEY)
        return {"base_url": f"http://localhost:{NEW_PORT}/api/v1", "started": True}

    monkeypatch.setattr("gaia.daemon.client.ensure_lemonade", daemon_restarts)
    chat = _cloud_session()
    server.live_pids = set()

    chat.chat([{"role": "user", "content": "after the crash"}])

    assert [call[:2] for call in gateway.calls] == [
        ("GET", f"http://localhost:{NEW_PORT}/api/v1/system-info"),
        ("POST", f"http://localhost:{NEW_PORT}/api/v1/cloud/auth"),
    ]


def test_a_local_model_never_touches_cloud_keys(server, monkeypatch):
    gateway = _Gateway(server, monkeypatch)
    chat = _chat_session()
    server.live_pids = {2}
    server.record(pid=2, port=NEW_PORT, key=NEW_KEY)

    chat.chat([{"role": "user", "content": "hi"}])

    assert gateway.calls == []


def test_a_failed_key_replay_is_logged_and_the_turn_still_runs(
    server, monkeypatch, caplog
):
    from gaia.llm.cloud_keys import CloudKeyError

    def store_unreadable(provider, client=None):
        raise CloudKeyError("the credential store is locked")

    monkeypatch.setattr("gaia.llm.cloud_keys.ensure_authenticated", store_unreadable)
    chat = _cloud_session()
    server.live_pids = {2}
    server.record(pid=2, port=NEW_PORT, key=NEW_KEY)

    with caplog.at_level("WARNING"):
        chat.chat([{"role": "user", "content": "hi"}])

    assert "credential store is locked" in caplog.text
    assert _last_request(server)[0].startswith(f"http://localhost:{NEW_PORT}/")


DEFAULT_PORT_URL = "http://localhost:13305/api/v1"
NEW_URL = f"http://localhost:{NEW_PORT}/api/v1"


class _CloudLemonade:
    """Every cloud-provider call GAIA makes, wherever it goes.

    Port 13305 stands for the user's own Lemonade: it answers like a real
    server, so a request there would succeed and hand it the key.
    """

    def __init__(self, monkeypatch):
        self.calls = []
        monkeypatch.setattr("gaia.llm.gateway.recall_token", lambda: "kept-amd-key")
        monkeypatch.setattr(
            "gaia.llm.cloud_keys.recall_key", lambda provider: f"kept-{provider}-key"
        )
        monkeypatch.setattr("gaia.llm.gateway.requests.request", self.request)

    def request(self, method, url, json=None, headers=None, **_):
        self.calls.append((method, url, (headers or {}).get("Authorization"), json))
        if url.endswith("/system-info"):
            entries = [
                {"name": name, "runtime_key_set": False, "models_discovered": 0}
                for name in ("amd", "fireworks")
            ]
            return _JSONResponse({"cloud": {"providers": entries}})
        return _JSONResponse({"models_discovered": 3})

    def keys_sent(self):
        return [(url, body["api_key"]) for _, url, _, body in self.calls if body]


def _restart_in_progress(server, monkeypatch, *, comes_back=True):
    """GAIA's server is gone; the daemon is bringing it back on a new port."""
    _install_embedded(server)
    server.live_pids = set()

    def daemon_restarts(*args, **kwargs):
        server.daemon_calls += 1
        if comes_back:
            server.live_pids = {2}
            server.record(pid=2, port=NEW_PORT, key=NEW_KEY)
        return {"base_url": NEW_URL, "started": True}

    monkeypatch.setattr("gaia.daemon.client.ensure_lemonade", daemon_restarts)


@pytest.mark.parametrize("provider", ["amd", "fireworks"])
def test_a_cloud_key_replay_during_a_restart_waits_for_gaias_server(
    server, monkeypatch, provider
):
    """The replay a relayed turn runs must never hand the key to port 13305."""
    from gaia.llm.cloud_keys import ensure_authenticated

    lemonade = _CloudLemonade(monkeypatch)
    _restart_in_progress(server, monkeypatch)

    assert ensure_authenticated(provider) is True

    assert server.daemon_calls == 1
    assert lemonade.calls, "the replay never reached GAIA's server"
    assert all(url.startswith(NEW_URL) for _, url, _, _ in lemonade.calls)
    assert {auth for _, _, auth, _ in lemonade.calls} == {f"Bearer {NEW_KEY}"}
    assert lemonade.keys_sent() == [(f"{NEW_URL}/cloud/auth", f"kept-{provider}-key")]


def test_a_relayed_cloud_turn_during_a_restart_never_targets_the_default_port(
    server, monkeypatch
):
    pytest.importorskip("fastapi")
    from gaia.ui._chat_helpers import _replay_cloud_key_for_relay

    lemonade = _CloudLemonade(monkeypatch)
    _restart_in_progress(server, monkeypatch)
    emitted = []
    sse = type("SSE", (), {"_emit": lambda self, event: emitted.append(event)})()

    _replay_cloud_key_for_relay(sse, CLOUD_MODEL)

    assert emitted == []
    assert lemonade.keys_sent() == [(f"{NEW_URL}/cloud/auth", "kept-amd-key")]
    assert not any(":13305" in url for _, url, _, _ in lemonade.calls)


@pytest.mark.parametrize("provider", ["amd", "fireworks"])
def test_a_restart_the_daemon_cannot_finish_sends_no_key_anywhere(
    server, monkeypatch, provider
):
    from gaia.daemon.errors import DaemonError
    from gaia.llm.cloud_keys import CloudKeyError, ensure_authenticated
    from gaia.llm.gateway import GatewayError

    lemonade = _CloudLemonade(monkeypatch)
    _restart_in_progress(server, monkeypatch)

    def daemon_cannot(*args, **kwargs):
        raise DaemonError("lemond exited during start; read lemond.log")

    monkeypatch.setattr("gaia.daemon.client.ensure_lemonade", daemon_cannot)

    with pytest.raises((CloudKeyError, GatewayError), match="read lemond.log"):
        ensure_authenticated(provider)
    assert lemonade.calls == []


def test_a_server_that_dies_again_mid_restart_is_reported_not_guessed(
    server, monkeypatch
):
    from gaia.llm.cloud_keys import CloudKeyError, ensure_authenticated

    lemonade = _CloudLemonade(monkeypatch)
    _restart_in_progress(server, monkeypatch, comes_back=False)

    with pytest.raises(CloudKeyError, match="restarting.*Try again in a moment"):
        ensure_authenticated("fireworks")
    assert lemonade.calls == []


def test_a_client_aimed_elsewhere_never_gets_a_key_while_gaia_runs_lemonade(
    server, monkeypatch
):
    """An explicit key opts a client out of following, not into the default port."""
    from gaia.llm.cloud_keys import CloudKeyError, ensure_authenticated

    lemonade = _CloudLemonade(monkeypatch)
    _install_embedded(server)
    client = lc.LemonadeClient(base_url=DEFAULT_PORT_URL, api_key="user-key")

    with pytest.raises(CloudKeyError, match="not GAIA's Lemonade Server"):
        ensure_authenticated("fireworks", client=client)
    assert lemonade.calls == []


def test_the_users_configured_server_still_gets_its_key(server, monkeypatch):
    from gaia.llm.cloud_keys import ensure_authenticated

    lemonade = _CloudLemonade(monkeypatch)
    _install_embedded(server)
    monkeypatch.setenv("LEMONADE_BASE_URL", "http://localhost:13305")
    server.live_pids = set()

    assert ensure_authenticated("fireworks") is True
    assert lemonade.keys_sent() == [
        (f"{DEFAULT_PORT_URL}/cloud/auth", "kept-fireworks-key")
    ]
    assert server.daemon_calls == 0


def test_a_url_resolved_mid_restart_still_follows_gaias_server(server):
    """The server came back between resolving the URL and building the client."""
    _install_embedded(server)
    server.live_pids = set()
    resolved_while_down = lc.resolve_lemonade_base_url()
    assert resolved_while_down == DEFAULT_PORT_URL

    server.live_pids = {2}
    server.record(pid=2, port=NEW_PORT, key=NEW_KEY)
    chat = LemonadeProvider(model=MODEL, base_url=resolved_while_down)
    chat.chat([{"role": "user", "content": "hi"}])

    assert _last_request(server) == (f"{NEW_URL}/chat/completions", f"Bearer {NEW_KEY}")


def test_the_manager_never_settles_on_the_default_port_after_starting(
    server, monkeypatch
):
    """The server stopped again between the daemon starting it and the re-read."""
    _serve_status(monkeypatch)
    monkeypatch.setattr("gaia.llm.lemonade_manager.gaia_runs_lemonade", lambda *a: True)
    monkeypatch.setattr(
        LemonadeManager, "start_embedded_if_stopped", staticmethod(lambda: True)
    )
    _restart_in_progress(server, monkeypatch)

    assert LemonadeManager.ensure_ready(min_context_size=65536, model=MODEL)
    assert LemonadeManager.get_base_url() == NEW_URL
