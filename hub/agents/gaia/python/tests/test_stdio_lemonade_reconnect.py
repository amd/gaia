# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""``/model`` waits for GAIA's restarted model server instead of failing (#5261).

A new TUI session switched to a cloud model just as GAIA's own Lemonade Server
stopped answering; the daemon brought it back seconds later, but the switch had
already ended the turn with "Lemonade Server is not reachable". The switch now
shows "Reconnecting to the model server…" and goes ahead once it is back.
"""

import io
import json
from pathlib import Path

import pytest
import requests
from gaia_agent import stdio

from gaia.daemon.instance import DaemonInstance
from gaia.llm import lemonade_client as lc
from gaia.llm import lemonade_service
from gaia.llm.lemonade_client import LemonadeClientError
from gaia.llm.lemonade_embedded import EmbeddedLemonade

CLOUD_MODEL = "amd.DeepSeek-V4.1-Flash"
OLD_URL = "http://localhost:60293/api/v1"
NEW_URL = "http://localhost:60411/api/v1"


class _Config:
    def __init__(self):
        self.use_claude = False
        self.model = "Gemma-4-E4B-it-GGUF"
        self.claude_model = None
        self.base_url = OLD_URL
        self.system_prompt = None


class _Chat:
    def __init__(self, llm_client):
        self.config = _Config()
        self.llm_client = llm_client

    @property
    def effective_model(self):
        return self.config.claude_model if self.config.use_claude else self.config.model


class _Agent:
    def __init__(self, llm_client):
        self.chat = _Chat(llm_client)
        self.config = None
        self._use_claude = False
        self.model_id = self.chat.config.model
        self.rebuilt = 0

    def rebuild_system_prompt(self):
        self.rebuilt += 1


class _WaitingClient:
    """The live Lemonade client, as the switch sees it.

    *outcome*: True waits and follows the server to NEW_URL; False is a server
    that is not GAIA's; "back" is GAIA's server already answering where the
    client points; an exception is raised from the wait.
    """

    def __init__(self, outcome):
        self.url = OLD_URL
        self.outcome = outcome
        self.waits = []
        self.follows_gaia_lemonade = outcome is not False

    def current_base_url(self):
        return self.url

    def wait_for_gaia_lemonade(self, model=None, *, on_status=None):
        self.waits.append(model)
        if isinstance(self.outcome, Exception):
            if on_status:
                on_status(lc.RECONNECTING_MESSAGE)
            raise self.outcome
        if self.outcome == "back":
            return False
        if self.outcome:
            on_status(lc.RECONNECTING_MESSAGE)
            self.url = NEW_URL
        return self.outcome


@pytest.fixture
def quiet_header(monkeypatch):
    """The post-switch status ping probes Lemonade; keep it off the network."""
    monkeypatch.setattr(stdio, "_lemonade_health", lambda url: {})
    monkeypatch.setattr(stdio, "running_embedded_lemonade_url", lambda: None)


def _run(agent, target):
    out = io.StringIO()
    stdio.run_model_command(agent, f"/model {target}", out)
    return [json.loads(line) for line in out.getvalue().splitlines() if line]


def _unreachable_until_moved(listed):
    def _models(base_url):
        listed.append(base_url)
        if base_url == OLD_URL:
            raise RuntimeError(f"Lemonade Server is not reachable at {base_url}.")
        return [CLOUD_MODEL]

    return _models


class TestTheSwitchWaits:
    def test_a_switch_during_a_restart_shows_reconnecting_then_succeeds(
        self, monkeypatch, quiet_header
    ):
        listed, built = [], []
        monkeypatch.setattr(stdio, "_lemonade_models", _unreachable_until_moved(listed))
        monkeypatch.setattr(
            stdio, "create_client", lambda **kw: built.append(kw) or object()
        )
        live = _WaitingClient(outcome=True)
        agent = _Agent(live)

        events = _run(agent, CLOUD_MODEL)

        assert events[0] == {
            "type": "status",
            "status": "working",
            "message": "Reconnecting to the model server…",
            "phase": "reconnecting",
        }
        assert events[-1]["type"] == "final"
        assert live.waits == [CLOUD_MODEL]
        assert listed == [OLD_URL, NEW_URL]
        assert built[0]["base_url"] == NEW_URL
        assert agent.chat.config.model == CLOUD_MODEL

    def test_a_server_that_never_returns_ends_the_switch_with_why(
        self, monkeypatch, quiet_header
    ):
        listed = []
        monkeypatch.setattr(stdio, "_lemonade_models", _unreachable_until_moved(listed))
        live = _WaitingClient(
            outcome=LemonadeClientError(
                f"GAIA's Lemonade Server at {OLD_URL} stopped answering and was not "
                "back within 60s. Run `gaia daemon status`."
            )
        )
        agent = _Agent(live)

        events = _run(agent, CLOUD_MODEL)

        assert [e["type"] for e in events] == ["status", "error"]
        assert "was not back within 60s" in events[-1]["detail"]
        assert "gaia daemon status" in events[-1]["detail"]
        assert agent.chat.llm_client is live, "left on the previous model"
        assert agent.rebuilt == 0
        assert listed == [OLD_URL]

    def test_a_server_back_before_the_wait_is_listed_again(
        self, monkeypatch, quiet_header
    ):
        """It restarted on its port while the failed listing was still
        retrying: there is nothing left to wait for, so the switch lists again."""
        attempts = []

        def _models(base_url):
            attempts.append(base_url)
            if len(attempts) == 1:
                raise RuntimeError(f"Lemonade Server is not reachable at {base_url}.")
            return [CLOUD_MODEL]

        monkeypatch.setattr(stdio, "_lemonade_models", _models)
        monkeypatch.setattr(stdio, "create_client", lambda **kw: object())
        live = _WaitingClient(outcome="back")
        agent = _Agent(live)

        events = _run(agent, CLOUD_MODEL)

        assert events[-1]["type"] == "final"
        assert attempts == [OLD_URL, OLD_URL]
        assert live.waits == [CLOUD_MODEL]
        assert agent.chat.config.model == CLOUD_MODEL

    def test_a_server_that_is_not_gaias_keeps_its_own_error(
        self, monkeypatch, quiet_header
    ):
        """The user's or a remote server: nothing to wait for, no second list."""
        listed = []
        monkeypatch.setattr(stdio, "_lemonade_models", _unreachable_until_moved(listed))
        live = _WaitingClient(outcome=False)

        events = _run(_Agent(live), CLOUD_MODEL)

        assert [e["type"] for e in events] == ["error"]
        assert "not reachable at" in events[0]["detail"]
        assert listed == [OLD_URL]
        assert live.waits == [], "a server that is not GAIA's is never waited on"

    def test_from_claude_it_waits_through_a_client_for_the_configured_server(
        self, monkeypatch, quiet_header
    ):
        made = []

        class _Fresh(_WaitingClient):
            def __init__(self, base_url=None, verbose=True):
                super().__init__(outcome=True)
                made.append(base_url)

        listed = []
        monkeypatch.setattr(stdio, "LemonadeClient", _Fresh)
        monkeypatch.setattr(stdio, "_lemonade_models", _unreachable_until_moved(listed))
        monkeypatch.setattr(stdio, "create_client", lambda **kw: object())
        agent = _Agent(llm_client=object())  # a Claude client: no Lemonade URL

        events = _run(agent, CLOUD_MODEL)

        assert events[-1]["type"] == "final"
        assert made == [OLD_URL]
        assert listed == [OLD_URL, NEW_URL]


class TestTheRegression:
    """Through a real ``LemonadeClient``: the session in the issue, end to end.

    The previous session's teardown left GAIA's server alive but not answering;
    the next session switches to a cloud model. Mocks sit at the HTTP boundary
    (Lemonade and the daemon's ``/lemonade/ensure``) and the port probe.
    """

    def test_a_new_sessions_cloud_switch_survives_the_restart(
        self, tmp_path, monkeypatch, quiet_header
    ):
        monkeypatch.setenv("GAIA_HOME", str(tmp_path / "gaia-home"))
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path / "user"))
        for var in ("LEMONADE_BASE_URL", "LEMONADE_API_KEY", "GAIA_LEMONADE_EMBEDDED"):
            monkeypatch.delenv(var, raising=False)
        embedded = EmbeddedLemonade()
        embedded.root.mkdir(parents=True)

        def record(pid, port, key):
            embedded.state_path.write_text(
                json.dumps({"pid": pid, "port": port, "api_key": key, "version": "v"})
            )

        live_pids, listening = {1}, set()
        record(1, 60293, "old-key")
        daemon = DaemonInstance(pid=4343, port=55124, token="daemon-token")
        ensure_calls = []

        class _Http:
            def __init__(self, status, body, headers=None):
                self.status_code, self._body = status, body
                self.headers, self.text = headers or {}, json.dumps(body)

            def json(self):
                return self._body

        def post(url, json=None, headers=None, timeout=None):
            assert url == f"{daemon.base_url}/daemon/v1/lemonade/ensure"
            assert headers == {"Authorization": "Bearer daemon-token"}
            ensure_calls.append(url)
            if len(ensure_calls) == 1:
                return _Http(
                    503, {"detail": "running but not answering"}, {"Retry-After": "2"}
                )
            live_pids.clear()
            live_pids.add(2)
            listening.add(60411)
            record(2, 60411, "new-key")
            return _Http(200, {"base_url": NEW_URL, "port": 60411, "started": True})

        def get(url, headers=None, timeout=None):
            port = int(url.split("://", 1)[1].split("/", 1)[0].rsplit(":", 1)[1])
            if port not in listening:
                raise requests.exceptions.ConnectionError(f"refused on {port}")
            assert headers["Authorization"] == "Bearer new-key"
            return _Http(200, {"data": [{"id": CLOUD_MODEL, "labels": ["chat"]}]})

        def connect(address, timeout=None):
            if address[1] not in listening:
                raise ConnectionRefusedError(address[1])

            class _S:
                def __enter__(self):
                    return self

                def __exit__(self, *exc):
                    return False

            return _S()

        clock = [0.0]
        monkeypatch.setattr(
            "gaia.llm.lemonade_embedded.pid_exists", lambda pid: pid in live_pids
        )
        monkeypatch.setattr(EmbeddedLemonade, "is_installed", lambda self: True)
        monkeypatch.setattr(requests, "post", post)
        monkeypatch.setattr(requests, "get", get)
        monkeypatch.setattr(lc.socket, "create_connection", connect)
        monkeypatch.setattr("gaia.daemon.client.start_or_attach", lambda: daemon)
        monkeypatch.setattr(lemonade_service, "lease_gaia_lemonade", lambda *a: None)
        monkeypatch.setattr(
            lc.LemonadeClient, "_reconnect_clock", staticmethod(lambda: clock[0])
        )
        monkeypatch.setattr(
            lc.LemonadeClient,
            "_reconnect_sleep",
            staticmethod(lambda s: clock.__setitem__(0, clock[0] + s)),
        )
        replayed = []
        monkeypatch.setattr(
            "gaia.llm.cloud_keys.ensure_authenticated",
            lambda provider, client=None: replayed.append(provider) or True,
        )
        built = []
        monkeypatch.setattr(
            stdio, "create_client", lambda **kw: built.append(kw) or object()
        )
        live = lc.LemonadeClient(base_url=lc.resolve_lemonade_base_url())
        assert live.follows_gaia_lemonade

        events = _run(_Agent(live), CLOUD_MODEL)

        assert events[0]["message"] == "Reconnecting to the model server…"
        assert events[-1]["type"] == "final", events
        assert built[0]["base_url"] == NEW_URL
        assert len(ensure_calls) == 2
        assert replayed == ["amd"], "the restarted server gets the kept cloud key"
        assert clock[0] < lc.GAIA_LEMONADE_RECONNECT_SECONDS
