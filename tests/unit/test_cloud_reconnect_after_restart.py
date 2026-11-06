# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""A cloud chat keeps working after GAIA's Lemonade Server restarts.

A restarted lemond forgets its cloud providers as well as their keys. GAIA gave
the key back only to a provider that was still registered, so after a crash,
update or daemon restart every Fireworks turn failed with "isn't deployed
there, was renamed…" until the user opened Settings > Model.

Mocks sit at the HTTP boundary (``requests.post`` / ``requests.request``) and
the credential store; the state file is the real one GAIA reads.
"""

import json

import pytest

from gaia.llm import lemonade_client as lc
from gaia.llm.lemonade_embedded import EmbeddedLemonade
from gaia.llm.lemonade_manager import LemonadeManager
from gaia.llm.providers.lemonade import (
    LemonadeCloudDisconnectedError,
    LemonadeCloudModelUnavailableError,
    LemonadeProvider,
    classify_lemonade_exception,
)

OLD_PORT, OLD_KEY = 41001, "key-before-restart"
NEW_PORT, NEW_KEY = 41002, "key-after-restart"
MODEL = "fireworks.deepseek-v4p1-flash"
SAVED_KEY = "fw_test_000"


class _Reply:
    content = b"{}"

    def __init__(self, status, body):
        self.status_code = status
        self._body = body
        self.headers = {}
        self.text = json.dumps(body)

    def json(self):
        return self._body


class _Lemond:
    """GAIA's embedded server: its state file, cloud-provider API and chat."""

    def __init__(self, embedded, monkeypatch):
        self.embedded = embedded
        self.live_pids = {1}
        self.calls = []
        self.registered = True
        self.key = SAVED_KEY
        self.deployed = True
        monkeypatch.setattr(
            "gaia.llm.lemonade_embedded.pid_exists", lambda pid: pid in self.live_pids
        )
        monkeypatch.setattr(lc.requests, "post", self.post)
        monkeypatch.setattr(lc.requests, "request", self.request)

    def record(self, pid, port, key):
        self.embedded.root.mkdir(parents=True, exist_ok=True)
        self.embedded.state_path.write_text(
            json.dumps({"pid": pid, "port": port, "api_key": key, "version": "v"})
        )

    def restart(self):
        """A new lemond: new pid, port and key, and no cloud providers."""
        self.live_pids = {2}
        self.record(pid=2, port=NEW_PORT, key=NEW_KEY)
        self.registered, self.key = False, None

    def request(self, method, url, json=None, headers=None, **_):
        route = url.rsplit("/api/v1/", 1)[1]
        self.calls.append((method, route, json))
        if route == "system-info":
            providers = []
            if self.registered:
                providers.append(
                    {
                        "name": "fireworks",
                        "runtime_key_set": bool(self.key),
                        "models_discovered": 40 if self.key else 0,
                    }
                )
            return _Reply(200, {"cloud": {"providers": providers}})
        if route == "install":
            assert json["provider"] == "fireworks"
            assert json["base_url"].startswith("https://")
            self.registered = True
            return _Reply(200, {})
        assert route == "cloud/auth", route
        assert self.registered, "a key was handed to an unregistered provider"
        self.key = json["api_key"]
        return _Reply(200, {"models_discovered": 40})

    def post(self, url, json=None, headers=None, timeout=None, **_):
        self.calls.append(("POST", url.rsplit("/api/v1/", 1)[1], None))
        if not (self.registered and self.key and self.deployed):
            return _Reply(404, {"error": {"message": "model not found"}})
        return _Reply(
            200,
            {"choices": [{"message": {"content": "hi"}, "finish_reason": "stop"}]},
        )

    def routes(self):
        return [route for _, route, _ in self.calls]


@pytest.fixture
def lemond(tmp_path, monkeypatch):
    monkeypatch.setenv("GAIA_HOME", str(tmp_path / "gaia-home"))
    for var in (
        "LEMONADE_BASE_URL",
        "LEMONADE_API_KEY",
        "GAIA_LEMONADE_EMBEDDED",
        "FIREWORKS_API_KEY",
        "LEMONADE_FIREWORKS_API_KEY",
    ):
        monkeypatch.delenv(var, raising=False)
    server = _Lemond(EmbeddedLemonade(), monkeypatch)
    server.record(pid=1, port=OLD_PORT, key=OLD_KEY)
    # `gaia init` installed GAIA's own server.
    server.embedded.daemon_path.parent.mkdir(parents=True, exist_ok=True)
    server.embedded.daemon_path.write_text("")
    monkeypatch.setattr("gaia.llm.cloud_keys.recall_key", lambda provider: SAVED_KEY)
    monkeypatch.setattr(lc.LemonadeClient, "_ensure_model_loaded", lambda *a, **k: None)
    # The recorded server answers; nothing listens on its port in a unit test.
    monkeypatch.setattr(lc.LemonadeClient, "_gaia_lemonade_gone", lambda self: False)
    LemonadeManager.reset()
    yield server
    LemonadeManager.reset()


def _chat():
    return LemonadeProvider(model=MODEL, base_url=lc.resolve_lemonade_base_url())


def _say(chat, text="hi"):
    return chat.chat([{"role": "user", "content": text}])


def test_a_restart_registers_fireworks_and_hands_the_key_back_before_the_turn(lemond):
    chat = _chat()
    assert _say(chat, "first") == "hi"
    lemond.calls.clear()

    lemond.restart()
    assert _say(chat, "second") == "hi"

    assert lemond.routes() == [
        "system-info",
        "install",
        "system-info",
        "cloud/auth",
        "chat/completions",
    ]
    assert ("POST", "cloud/auth", {"provider": "fireworks", "api_key": SAVED_KEY}) in (
        lemond.calls
    )


def test_a_404_while_fireworks_is_unregistered_reconnects_and_retries_once(lemond):
    lemond.restart()
    chat = _chat()  # built after the restart, so it never saw the old server

    assert _say(chat) == "hi"

    routes = lemond.routes()
    assert routes.count("chat/completions") == 2
    assert routes.count("install") == 1
    assert routes.count("cloud/auth") == 1
    assert routes.index("cloud/auth") < len(routes) - 1


def test_a_404_while_keyless_with_no_key_to_give_says_the_engine_restarted(
    lemond, monkeypatch
):
    monkeypatch.setattr("gaia.llm.cloud_keys.recall_key", lambda provider: None)
    lemond.restart()
    chat = _chat()

    with pytest.raises(Exception) as caught:
        _say(chat)

    classified = classify_lemonade_exception(caught.value)
    assert isinstance(classified, LemonadeCloudDisconnectedError)
    message = classified.user_message
    assert "restarted" in message and "Fireworks AI" in message
    assert "deployed" not in message and "renamed" not in message
    assert lemond.routes().count("chat/completions") == 1


def test_the_environment_key_is_handed_back_when_none_is_saved(lemond, monkeypatch):
    monkeypatch.setattr("gaia.llm.cloud_keys.recall_key", lambda provider: None)
    monkeypatch.setenv("FIREWORKS_API_KEY", "fw_test_000")
    lemond.restart()
    chat = _chat()

    assert _say(chat) == "hi"
    assert lemond.key == "fw_test_000"


def test_a_real_404_on_a_connected_provider_is_not_retried(lemond):
    lemond.deployed = False
    chat = _chat()

    with pytest.raises(Exception) as caught:
        _say(chat)

    assert isinstance(
        classify_lemonade_exception(caught.value), LemonadeCloudModelUnavailableError
    )
    assert lemond.routes().count("chat/completions") == 1
    assert "install" not in lemond.routes() and "cloud/auth" not in lemond.routes()


def test_a_404_that_survives_the_reconnect_is_reported_as_the_model(lemond):
    lemond.restart()
    lemond.deployed = False
    chat = _chat()

    with pytest.raises(Exception) as caught:
        _say(chat)

    assert isinstance(
        classify_lemonade_exception(caught.value), LemonadeCloudModelUnavailableError
    )
    assert lemond.routes().count("chat/completions") == 2


def test_a_streamed_turn_reconnects_and_retries_once(lemond, monkeypatch):
    lemond.restart()
    starts = []

    def stream(self, *, model, **_):
        starts.append(model)
        if not (lemond.registered and lemond.key):
            raise lc.CloudModelUnavailableError(
                "404", model_id=model, provider="fireworks"
            )
        yield {"choices": [{"delta": {"content": "hi"}, "finish_reason": "stop"}]}

    monkeypatch.setattr(
        lc.LemonadeClient, "_stream_chat_completions_with_openai", stream
    )
    chat = _chat()

    assert "".join(chat.chat([{"role": "user", "content": "hi"}], stream=True)) == "hi"
    assert len(starts) == 2
    assert lemond.routes().count("cloud/auth") == 1


def test_the_users_own_server_is_never_registered_or_keyed(lemond, monkeypatch):
    from gaia.llm import cloud_keys

    lemond.restart()
    # The user saved their own server (external mode) at this address.
    url = f"http://localhost:{NEW_PORT}/api/v1"
    monkeypatch.setattr(lc, "configured_lemonade_url", lambda: url)
    monkeypatch.setattr("gaia.llm.lemonade_choice.is_users_server", lambda u: True)
    client = lc.LemonadeClient(base_url=lc.resolve_lemonade_base_url())

    with pytest.raises(cloud_keys.CloudKeyError):
        cloud_keys.reconnect_provider("fireworks", client=client)

    assert "install" not in lemond.routes() and "cloud/auth" not in lemond.routes()


def test_the_lost_connection_ends_the_turn_with_the_agent_ui_fix(lemond, monkeypatch):
    from gaia.agents.base.agent import Agent
    from gaia.llm.surface_hints import AGENT_UI_PROVIDER_SETTINGS, surface_wording

    monkeypatch.setattr("gaia.llm.cloud_keys.recall_key", lambda provider: None)
    lemond.restart()

    with pytest.raises(Exception) as caught:
        _say(_chat())

    message = Agent._cloud_account_refusal(None, caught.value)
    assert message and "restarted" in message
    assert AGENT_UI_PROVIDER_SETTINGS in surface_wording(message, "agent_ui")


def test_a_stream_that_already_answered_is_never_replayed(lemond, monkeypatch):
    starts = []

    def stream(self, *, model, **_):
        starts.append(model)
        yield {"choices": [{"delta": {"content": "partial"}, "finish_reason": None}]}
        lemond.restart()
        raise lc.CloudModelUnavailableError("404", model_id=model, provider="fireworks")

    monkeypatch.setattr(
        lc.LemonadeClient, "_stream_chat_completions_with_openai", stream
    )

    with pytest.raises(Exception):
        "".join(_chat().chat([{"role": "user", "content": "hi"}], stream=True))
    assert len(starts) == 1
    assert "install" not in lemond.routes() and "cloud/auth" not in lemond.routes()


def test_local_only_sends_no_registration_or_key(lemond, monkeypatch):
    from gaia.llm import cloud_keys

    lemond.restart()
    monkeypatch.setattr(cloud_keys, "cloud_blocked", lambda: True)
    client = lc.LemonadeClient(base_url=lc.resolve_lemonade_base_url())

    with pytest.raises(cloud_keys.CloudKeyBlockedError):
        cloud_keys.reconnect_provider("fireworks", client=client)
    assert lemond.calls == []


def test_a_lemonade_gaia_does_not_run_is_never_registered(lemond, monkeypatch):
    from gaia.llm import cloud_keys

    lemond.restart()
    lemond.embedded.daemon_path.unlink()
    monkeypatch.setenv("LEMONADE_BASE_URL", f"http://localhost:{NEW_PORT}/api/v1")
    client = lc.LemonadeClient(base_url=lc.resolve_lemonade_base_url())

    assert cloud_keys.reconnect_provider("fireworks", client=client) is False
    assert "install" not in lemond.routes() and "cloud/auth" not in lemond.routes()


def test_a_lost_gateway_is_not_blamed_on_its_key(lemond, monkeypatch):
    lemond.restart()
    monkeypatch.setattr("gaia.llm.gateway.recall_token", lambda: "kept-amd-key")
    chat = LemonadeProvider(
        model="amd.DeepSeek-V4.1-Flash", base_url=lc.resolve_lemonade_base_url()
    )

    with pytest.raises(Exception) as caught:
        _say(chat)

    message = classify_lemonade_exception(caught.value).user_message
    assert "restarted" in message and "working key" not in message
    assert "install" not in lemond.routes()
