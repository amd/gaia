# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""A turn survives GAIA's Lemonade Server being stopped under it.

The daemon stops GAIA's server once it is idle. A stop that lands under a
process still using it must cost that process a restart, not the turn: the
client has the daemon start the server again and sends the request once more.

Mocks sit at the HTTP boundary (``requests.post``), the daemon boundary
(``ensure_lemonade``) and the port probe; the state file is the real one GAIA
reads.
"""

import json
import threading
import time

import pytest
import requests

from gaia.llm import lemonade_client as lc
from gaia.llm import lemonade_service
from gaia.llm.lemonade_embedded import EmbeddedLemonade, EmbeddedStatus
from gaia.llm.lemonade_manager import LemonadeManager

OLD_PORT, OLD_KEY = 41001, "key-before-stop"
NEW_PORT, NEW_KEY = 41002, "key-after-restart"
MODEL = "Gemma-4-E4B-it-GGUF"


class _Response:
    status_code = 200

    def json(self):
        return {"choices": [{"message": {"content": "hi"}, "finish_reason": "stop"}]}


class Server:
    """GAIA's embedded server as its state file, port and HTTP surface show it."""

    def __init__(self, embedded: EmbeddedLemonade, monkeypatch):
        self.embedded = embedded
        self.live_pids = {1}
        self.listening = {OLD_PORT}
        self.requests = []
        self.daemon_calls = 0
        # Runs before a request is answered; may stop the server under it.
        self.on_request = None
        monkeypatch.setattr(
            "gaia.llm.lemonade_embedded.pid_exists", lambda pid: pid in self.live_pids
        )

    def record(self, pid: int, port: int, key: str) -> None:
        self.embedded.root.mkdir(parents=True, exist_ok=True)
        self.embedded.state_path.write_text(
            json.dumps({"pid": pid, "port": port, "api_key": key, "version": "v"})
        )

    def stop(self) -> None:
        """What an idle stop leaves: no process, no listener, no state."""
        self.live_pids = set()
        self.listening = set()
        self.embedded.state_path.unlink(missing_ok=True)

    def restart(self, *args, **kwargs) -> dict:
        """The daemon's ``ensure_lemonade``."""
        self.daemon_calls += 1
        self.live_pids = {2}
        self.listening = {NEW_PORT}
        self.record(pid=2, port=NEW_PORT, key=NEW_KEY)
        return {"base_url": f"http://localhost:{NEW_PORT}/api/v1", "started": True}

    def connect(self, address, timeout=None):
        if address[1] not in self.listening:
            raise ConnectionRefusedError(f"nothing listens on {address[1]}")
        return _Socket()

    def post(self, url, json=None, headers=None, timeout=None):
        if self.on_request:
            hook, self.on_request = self.on_request, None
            hook()
        port = int(url.split("://", 1)[1].split("/", 1)[0].rsplit(":", 1)[1])
        if port not in self.listening:
            raise requests.exceptions.ConnectionError(f"refused on {port}")
        self.requests.append((url, (headers or {}).get("Authorization")))
        return _Response()


class _Socket:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


@pytest.fixture
def server(tmp_path, monkeypatch):
    monkeypatch.setenv("GAIA_HOME", str(tmp_path / "gaia-home"))
    for var in ("LEMONADE_BASE_URL", "LEMONADE_API_KEY", "GAIA_LEMONADE_EMBEDDED"):
        monkeypatch.delenv(var, raising=False)
    s = Server(EmbeddedLemonade(), monkeypatch)
    s.record(pid=1, port=OLD_PORT, key=OLD_KEY)
    monkeypatch.setattr(lc.requests, "post", s.post)
    monkeypatch.setattr(lc.socket, "create_connection", s.connect)
    monkeypatch.setattr("gaia.daemon.client.ensure_lemonade", s.restart)
    monkeypatch.setattr(lc.LemonadeClient, "_ensure_model_loaded", lambda *a, **k: None)
    monkeypatch.setattr(lc.LemonadeClient, "_retry_sleep", staticmethod(lambda s: None))
    monkeypatch.setattr(lemonade_service, "lease_gaia_lemonade", lambda *a: None)
    LemonadeManager.reset()
    yield s
    LemonadeManager.reset()


def _client():
    return lc.LemonadeClient(model=MODEL, base_url=lc.resolve_lemonade_base_url())


def _ask(client, **kwargs):
    return client.chat_completions(
        MODEL, [{"role": "user", "content": "hi"}], auto_download=False, **kwargs
    )


class TestTheTurnSurvives:
    def test_a_stop_during_the_request_restarts_and_answers(self, server):
        client = _client()
        server.on_request = server.stop

        result = _ask(client)

        assert result["choices"][0]["message"]["content"] == "hi"
        assert server.daemon_calls == 1
        assert server.requests == [
            (
                f"http://localhost:{NEW_PORT}/api/v1/chat/completions",
                f"Bearer {NEW_KEY}",
            )
        ]

    def test_a_restart_by_another_process_mid_request_is_followed(self, server):
        """The other process's restart minted a new key: no 401 for this one."""
        client = _client()

        def _replaced():
            server.stop()
            server.restart()

        server.on_request = _replaced
        _ask(client)

        assert server.daemon_calls == 1, "this client must not start a second one"
        assert server.requests[-1][1] == f"Bearer {NEW_KEY}"

    def test_a_stream_stopped_before_its_first_chunk_starts_over(
        self, server, monkeypatch
    ):
        calls = []

        def stream_chunks(self, **kwargs):
            calls.append(self.port)
            if self.port not in server.listening:
                raise lc.LemonadeClientError("Streaming request failed: refused")
            yield {"choices": [{"delta": {"content": "hi"}, "finish_reason": "stop"}]}

        monkeypatch.setattr(lc.LemonadeClient, "_stream_chat_chunks", stream_chunks)
        client = _client()
        server.stop()
        server.record(pid=1, port=OLD_PORT, key=OLD_KEY)  # stale, still alive
        server.live_pids = {1}

        chunks = list(_ask(client, stream=True))

        assert [c["choices"][0]["delta"]["content"] for c in chunks] == ["hi"]
        assert calls == [OLD_PORT, NEW_PORT]

    def test_a_stream_under_way_is_never_replayed(self, server, monkeypatch):
        def stream_chunks(self, **kwargs):
            yield {"choices": [{"delta": {"content": "half"}, "finish_reason": None}]}
            server.stop()
            raise lc.LemonadeClientError("Streaming request failed: reset")

        monkeypatch.setattr(lc.LemonadeClient, "_stream_chat_chunks", stream_chunks)
        stream = _ask(_client(), stream=True)

        assert next(stream)["choices"][0]["delta"]["content"] == "half"
        with pytest.raises(lc.LemonadeClientError, match="reset"):
            next(stream)
        assert server.daemon_calls == 0

    def test_another_failure_is_not_mistaken_for_a_stop(self, server, monkeypatch):
        class _Refused:
            status_code = 500
            text = "model crashed"
            headers = {}

        monkeypatch.setattr(lc.requests, "post", lambda *a, **k: _Refused())
        with pytest.raises(lc.LemonadeClientError):
            _ask(_client())
        assert server.daemon_calls == 0

    def test_a_restart_that_fails_is_the_turns_error(self, server, monkeypatch):
        from gaia.daemon.errors import DaemonError

        def daemon_cannot(*a, **k):
            raise DaemonError("lemond exited during start; read lemond.log")

        monkeypatch.setattr("gaia.daemon.client.ensure_lemonade", daemon_cannot)
        server.on_request = server.stop
        with pytest.raises(lc.LemonadeClientError, match="read lemond.log"):
            _ask(_client())

    def test_a_users_own_server_is_never_restarted(self, server, monkeypatch):
        monkeypatch.setenv("LEMONADE_BASE_URL", f"http://localhost:{OLD_PORT}")
        client = _client()
        server.on_request = server.stop
        with pytest.raises(requests.exceptions.ConnectionError):
            _ask(client)
        assert server.daemon_calls == 0


class TestEveryClientLeases:
    def test_a_client_of_gaias_server_leases_it(self, server, monkeypatch):
        leased = []
        monkeypatch.setattr(lemonade_service, "lease_gaia_lemonade", leased.append)
        _client()
        assert leased == [None]  # this process's own daemon

    def test_a_client_of_the_users_server_does_not(self, server, monkeypatch):
        monkeypatch.setenv("LEMONADE_BASE_URL", "http://localhost:13305")
        leased = []
        monkeypatch.setattr(lemonade_service, "lease_gaia_lemonade", leased.append)
        _client()
        assert leased == []


class TestAnotherGaiaHome:
    """A process under its own GAIA_HOME using the default home's server."""

    @pytest.fixture
    def default_home(self, server, tmp_path, monkeypatch):
        from pathlib import Path

        user = tmp_path / "user"
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: user))
        default = EmbeddedLemonade(home=user / ".gaia")
        server.embedded = default
        server.record(pid=1, port=OLD_PORT, key=OLD_KEY)
        monkeypatch.setenv("GAIA_HOME", str(tmp_path / "eval-home"))
        monkeypatch.setenv("LEMONADE_BASE_URL", f"http://127.0.0.1:{OLD_PORT}")
        monkeypatch.setenv("LEMONADE_API_KEY", OLD_KEY)
        return user / ".gaia"

    def test_it_leases_from_the_daemon_that_owns_the_server(
        self, default_home, monkeypatch
    ):
        leased = []
        monkeypatch.setattr(lemonade_service, "lease_gaia_lemonade", leased.append)
        client = lc.LemonadeClient(model=MODEL)
        assert client.follows_gaia_lemonade
        assert leased == [default_home / "host"]

    def test_it_restarts_through_that_daemon_and_takes_the_new_key(
        self, server, default_home, monkeypatch
    ):
        asked = []

        def owner_restarts(*args, host_dir=None, **kwargs):
            asked.append(host_dir)
            return server.restart()

        monkeypatch.setattr("gaia.daemon.client.ensure_lemonade", owner_restarts)
        client = lc.LemonadeClient(model=MODEL)
        server.on_request = server.stop

        _ask(client)

        assert asked == [default_home / "host"]
        assert server.requests[-1] == (
            f"http://localhost:{NEW_PORT}/api/v1/chat/completions",
            f"Bearer {NEW_KEY}",
        )


class TestOneServerWhenTwoRestart:
    def test_two_processes_restarting_at_once_start_one_server(
        self, gaia_owned_lemonade
    ):
        """The daemon serialises ensure(): the second finds the first's server."""
        from gaia.daemon.lemonade import EmbeddedLemonadeOwner

        started = []
        running = threading.Event()

        class _Embedded:
            version = "1"

            def is_installed(self):
                return True

            def status(self):
                if running.is_set():
                    return EmbeddedStatus(
                        installed=True,
                        running=True,
                        version="1",
                        port=NEW_PORT,
                        pid=2,
                        base_url=f"http://localhost:{NEW_PORT}/api/v1",
                        owner_pid=1,
                    )
                return EmbeddedStatus(installed=True, running=False, version="1")

            def start(self, **kwargs):
                started.append(1)
                time.sleep(0.05)
                running.set()
                return self.status()

            def claim(self, *a):
                pass

        owner = EmbeddedLemonadeOwner(factory=_Embedded, owner_pid=1)
        results = []
        threads = [
            threading.Thread(target=lambda: results.append(owner.ensure()))
            for _ in range(2)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(5)

        assert started == [1]
        assert sorted(r.started for r in results) == [False, True]
        assert {r.port for r in results} == {NEW_PORT}
