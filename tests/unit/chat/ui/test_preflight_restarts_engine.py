# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""A chat whose engine died starts it again before answering.

GAIA's embedded Lemonade was started by the daemon at launch. Killed while the
Agent UI stayed open, every later turn in an open chat failed with "Couldn't
reach the local LLM… try sending the message again", cloud chats included,
until the app was restarted: the turn's pre-flight probed the dead address
with a bare HTTP GET, so nothing ever asked the daemon to start it.

Mocks sit at the daemon boundary (``ensure_lemonade``), the HTTP probe
(``httpx.get``) and the TCP connect; the state file is the real one GAIA reads.
"""

import json
from contextlib import contextmanager

import httpx
import pytest

from gaia.daemon import broker_client
from gaia.daemon.errors import LemonadeStartFailedError
from gaia.llm import lemonade_choice
from gaia.llm import lemonade_client as lc
from gaia.llm.lemonade_client import RECONNECTING_MESSAGE, GaiaLemonadeStartError
from gaia.llm.lemonade_embedded import EmbeddedLemonade
from gaia.llm.lemonade_manager import LemonadeManager
from gaia.llm.providers.lemonade import (
    LemonadeNetworkError,
    LemonadeServerUnreachableError,
    classify_lemonade_exception,
    wording_for_model,
)
from gaia.ui import _chat_helpers as helpers

OLD_PORT, OLD_KEY = 41011, "key-before-the-crash"
NEW_PORT, NEW_KEY = 41012, "key-after-the-restart"
CLOUD = "fireworks.deepseek-v4p1-flash"
LOCAL = "Gemma-4-E4B-it-GGUF"
MISSING = r"C:\gaia\lemonade\dist\2026.44.1\lemond.exe is missing"


class _Sse:
    def __init__(self):
        self.events = []
        self.cancelled = None

    def _emit(self, event):
        self.events.append(event)


class Engine:
    """GAIA's embedded server as its state file, port and daemon show it."""

    def __init__(self, embedded: EmbeddedLemonade, monkeypatch):
        self.embedded = embedded
        self.live_pid = None
        self.live_port = None
        self.daemon_calls = 0
        self.health_urls = []
        self.resident = []
        self.keys_replayed = []
        monkeypatch.setattr(
            "gaia.llm.lemonade_embedded.pid_exists", lambda pid: pid == self.live_pid
        )

    def run(self, pid, port, key):
        self.embedded.root.mkdir(parents=True, exist_ok=True)
        self.embedded.state_path.write_text(
            json.dumps({"pid": pid, "port": port, "api_key": key, "version": "v"})
        )
        self.live_pid, self.live_port = pid, port

    def kill(self):
        self.live_pid = self.live_port = None

    def connect(self, address, timeout=None):
        if address[1] != self.live_port:
            raise ConnectionRefusedError(10061, "actively refused it")

        @contextmanager
        def _conn():
            yield None

        return _conn()

    def health(self, url, **_kwargs):
        self.health_urls.append(url)
        if f":{self.live_port}/" not in url:
            raise httpx.ConnectError(
                "[WinError 10061] No connection could be made because the "
                "target machine actively refused it"
            )
        resp = type("Resp", (), {"status_code": 200})()
        resp.json = lambda: {"all_models_loaded": list(self.resident)}
        return resp


@pytest.fixture
def engine(tmp_path, monkeypatch):
    monkeypatch.setenv("GAIA_HOME", str(tmp_path / "gaia-home"))
    for var in (
        "LEMONADE_BASE_URL",
        "LEMONADE_API_KEY",
        "GAIA_LEMONADE_EMBEDDED",
        "GAIA_EVAL_AGENT_PROVIDER",
    ):
        monkeypatch.delenv(var, raising=False)
    e = Engine(EmbeddedLemonade(), monkeypatch)
    monkeypatch.setattr(lc, "gaia_runs_lemonade", lambda *_a, **_k: True)
    monkeypatch.setattr(
        "gaia.llm.lemonade_service.lease_gaia_lemonade", lambda *_a, **_k: None
    )
    monkeypatch.setattr(lc.socket, "create_connection", e.connect)
    monkeypatch.setattr(httpx, "get", e.health)
    monkeypatch.setattr(
        lc.LemonadeClient,
        "_replay_cloud_key",
        lambda self, model: e.keys_replayed.append((model, self.port)) or True,
    )
    monkeypatch.setattr(lc, "_ENGINE_START_FAILURE", None)
    # The app launched with it running, and an open chat used it.
    e.run(pid=1, port=OLD_PORT, key=OLD_KEY)
    LemonadeManager.reset()
    LemonadeManager._base_url = f"http://localhost:{OLD_PORT}/api/v1"
    LemonadeManager._follows_gaia_lemonade = True
    LemonadeManager._ready_for_cloud = True
    yield e
    LemonadeManager.reset()


def _daemon_restarts(engine):
    def ensure_lemonade(*_args, **_kwargs):
        engine.daemon_calls += 1
        engine.run(pid=2, port=NEW_PORT, key=NEW_KEY)
        return {"base_url": f"http://localhost:{NEW_PORT}/api/v1", "started": True}

    return ensure_lemonade


def _daemon_cannot_start(engine):
    def ensure_lemonade(*_args, **_kwargs):
        engine.daemon_calls += 1
        raise LemonadeStartFailedError(
            f"The daemon could not start Lemonade Server: {MISSING}", MISSING
        )

    return ensure_lemonade


def test_a_cloud_turn_after_the_engine_died_starts_it_and_hands_back_the_key(
    engine, monkeypatch
):
    monkeypatch.setattr("gaia.daemon.client.ensure_lemonade", _daemon_restarts(engine))
    engine.kill()
    engine.resident = [{"model_name": CLOUD, "type": "llm", "recipe": "cloud"}]
    sse = _Sse()

    helpers._preflight_load(CLOUD, sse, lambda: False)

    assert engine.daemon_calls == 1
    assert engine.keys_replayed == [(CLOUD, NEW_PORT)]
    assert all(f":{NEW_PORT}/" in url for url in engine.health_urls)
    assert {"type": "status", "status": "info", "message": RECONNECTING_MESSAGE} in (
        sse.events
    )


def test_a_local_turn_after_the_engine_died_loads_on_the_restarted_server(
    engine, monkeypatch
):
    monkeypatch.setattr("gaia.daemon.client.ensure_lemonade", _daemon_restarts(engine))
    monkeypatch.setattr(lc, "resolve_ctx_size", lambda *_a, **_k: 65536)
    monkeypatch.setattr(lemonade_choice, "is_users_server", lambda _url: False)

    @contextmanager
    def lease(_model, **_kwargs):
        yield None

    monkeypatch.setattr(broker_client, "model_lease", lease)
    loaded = []

    def load(client, model, *_args, **_kwargs):
        loaded.append((model, client.port))
        return []

    monkeypatch.setattr(helpers, "_load_making_room", load)
    engine.kill()

    helpers._preflight_load(LOCAL, _Sse(), lambda: False)

    assert engine.daemon_calls == 1
    assert loaded == [(LOCAL, NEW_PORT)]


def test_a_running_engine_is_not_restarted(engine, monkeypatch):
    monkeypatch.setattr("gaia.daemon.client.ensure_lemonade", _daemon_restarts(engine))
    engine.resident = [{"model_name": CLOUD, "type": "llm", "recipe": "cloud"}]

    helpers._preflight_load(CLOUD, _Sse(), lambda: False)

    assert engine.daemon_calls == 0
    assert engine.keys_replayed == []


@pytest.mark.parametrize("model", [CLOUD, LOCAL])
def test_an_engine_that_will_not_start_says_why_and_how_to_repair_it(
    engine, monkeypatch, model
):
    monkeypatch.setattr(
        "gaia.daemon.client.ensure_lemonade", _daemon_cannot_start(engine)
    )
    engine.kill()

    with pytest.raises(GaiaLemonadeStartError) as raised:
        helpers._preflight_load(model, _Sse(), lambda: False)

    assert engine.daemon_calls == 1
    shown = helpers._agent_ui_wording(
        wording_for_model(classify_lemonade_exception(raised.value).user_message, model)
    )
    assert "GAIA's AI engine couldn't start" in shown
    assert MISSING in shown
    assert "Settings > Setup" in shown
    assert "`" not in shown and "gaia init" not in shown
    assert "local LLM" not in shown
    assert "Send the message again" not in shown


@pytest.mark.parametrize(
    "error", [LemonadeNetworkError(), LemonadeServerUnreachableError()]
)
def test_a_cloud_chat_is_never_told_its_llm_is_local(error):
    cloud = wording_for_model(error.user_message, CLOUD)
    assert "local" not in cloud.lower()
    assert "GAIA's AI engine" in cloud
    assert wording_for_model(error.user_message, LOCAL) == error.user_message


def test_the_agent_loop_words_a_server_failure_for_a_cloud_model():
    from gaia.agents.base.agent import Agent

    class _Agent(Agent):
        def _register_tools(self):
            pass

    agent = _Agent.__new__(_Agent)
    agent.model_id = CLOUD
    failure = RuntimeError("Connection refused by localhost:13305")

    shown = agent._extract_lemonade_user_message(failure)

    assert shown is not None and "local LLM" not in shown
    agent.model_id = LOCAL
    assert "local LLM" in agent._extract_lemonade_user_message(failure)
