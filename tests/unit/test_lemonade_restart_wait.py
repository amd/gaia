# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""A request waits for GAIA's restarted Lemonade Server instead of failing (#5261).

GAIA's own server can stop answering for a few seconds right after a start: the
daemon then reports it "running but not answering", and soon either serves it
again or starts a new one. A request that lands in that gap waits, bounded, and
goes ahead once the server is back. A server the user runs, or a remote one, is
never waited on or restarted, and nothing here ever stops a running server.

Mocks sit at the HTTP boundary (``requests.post`` for both Lemonade and the
daemon's ``/lemonade/ensure``), the port probe, and the clock. The state file is
the real one GAIA reads, and the daemon client is the real one.
"""

import json
from pathlib import Path

import pytest
import requests

from gaia.daemon.instance import DaemonInstance
from gaia.llm import lemonade_client as lc
from gaia.llm import lemonade_service
from gaia.llm.lemonade_embedded import (
    EmbeddedLemonade,
    EmbeddedLemonadeError,
    EmbeddedLemonadeNotAnswering,
    EmbeddedStatus,
)
from gaia.llm.lemonade_manager import LemonadeManager

OLD_PORT, OLD_KEY = 60293, "key-of-the-server-that-went-quiet"
NEW_PORT, NEW_KEY = 60411, "key-after-the-daemon-restarted-it"
MODEL = "Gemma-4-E4B-it-GGUF"
DAEMON = DaemonInstance(pid=4343, port=55124, token="daemon-token")
ENSURE_URL = f"{DAEMON.base_url}/daemon/v1/lemonade/ensure"
NOT_ANSWERING = (
    "GAIA's Lemonade Server (pid 1) is running but not answering; it may still "
    "be loading a model."
)


class _Http:
    def __init__(self, status, body, headers=None):
        self.status_code = status
        self._body = body
        self.headers = headers or {}
        self.text = json.dumps(body)

    def json(self):
        return self._body


class World:
    """GAIA's server as its state file, port and HTTP show it, plus the daemon."""

    def __init__(self, embedded: EmbeddedLemonade, monkeypatch):
        self.embedded = embedded
        self.live_pids = {1}
        self.listening = set()
        self.lemonade_posts = []
        self.daemon_posts = []
        # What each /lemonade/ensure answers, in order; the last one repeats.
        self.daemon_answers = []
        self.now = 0.0
        monkeypatch.setattr(
            "gaia.llm.lemonade_embedded.pid_exists", lambda pid: pid in self.live_pids
        )

    def record(self, pid: int, port: int, key: str) -> None:
        self.embedded.root.mkdir(parents=True, exist_ok=True)
        self.embedded.state_path.write_text(
            json.dumps({"pid": pid, "port": port, "api_key": key, "version": "v"})
        )

    # -- what the daemon does on /lemonade/ensure --------------------------

    def not_answering(self):
        return _Http(503, {"detail": NOT_ANSWERING}, {"Retry-After": "2"})

    def restarted(self):
        """The quiet server died; the daemon started a new one elsewhere."""
        self.live_pids = {2}
        self.listening = {NEW_PORT}
        self.record(pid=2, port=NEW_PORT, key=NEW_KEY)
        return _Http(
            200,
            {
                "base_url": f"http://localhost:{NEW_PORT}/api/v1",
                "port": NEW_PORT,
                "version": "v",
                "started": True,
            },
        )

    def came_back(self):
        """The same server answers again; the daemon reports it unchanged."""
        self.listening = {OLD_PORT}
        return _Http(
            200,
            {
                "base_url": f"http://localhost:{OLD_PORT}/api/v1",
                "port": OLD_PORT,
                "version": "v",
                "started": False,
            },
        )

    def cannot_start(self):
        return _Http(503, {"detail": "lemond exited during start; read lemond.log"})

    # -- boundaries --------------------------------------------------------

    def post(self, url, json=None, headers=None, timeout=None):
        if url.startswith(DAEMON.base_url):
            self.daemon_posts.append(
                {"url": url, "json": json, "headers": headers, "timeout": timeout}
            )
            answer = (
                self.daemon_answers.pop(0)
                if len(self.daemon_answers) > 1
                else self.daemon_answers[0]
            )
            return answer()
        port = int(url.split("://", 1)[1].split("/", 1)[0].rsplit(":", 1)[1])
        if port not in self.listening:
            raise requests.exceptions.ConnectionError(f"refused on {port}")
        self.lemonade_posts.append((url, (headers or {}).get("Authorization")))
        return _Http(
            200, {"choices": [{"message": {"content": "hi"}, "finish_reason": "stop"}]}
        )

    def connect(self, address, timeout=None):
        if address[1] not in self.listening:
            raise ConnectionRefusedError(f"nothing listens on {address[1]}")
        return _Socket()

    def clock(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


class _Socket:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


@pytest.fixture
def world(tmp_path, monkeypatch):
    monkeypatch.setenv("GAIA_HOME", str(tmp_path / "gaia-home"))
    # Never this machine's own ~/.gaia: its live server must not be adopted.
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path / "user"))
    for var in ("LEMONADE_BASE_URL", "LEMONADE_API_KEY", "GAIA_LEMONADE_EMBEDDED"):
        monkeypatch.delenv(var, raising=False)
    w = World(EmbeddedLemonade(), monkeypatch)
    # A server that was reported ready, then went quiet: alive, not listening.
    w.record(pid=1, port=OLD_PORT, key=OLD_KEY)
    monkeypatch.setattr(requests, "post", w.post)
    monkeypatch.setattr(lc.socket, "create_connection", w.connect)
    monkeypatch.setattr("gaia.daemon.client.start_or_attach", lambda: DAEMON)
    monkeypatch.setattr(lc.LemonadeClient, "_ensure_model_loaded", lambda *a, **k: None)
    monkeypatch.setattr(lc.LemonadeClient, "_retry_sleep", staticmethod(lambda s: None))
    monkeypatch.setattr(lc.LemonadeClient, "_reconnect_clock", staticmethod(w.clock))
    monkeypatch.setattr(lc.LemonadeClient, "_reconnect_sleep", staticmethod(w.sleep))
    monkeypatch.setattr(lemonade_service, "lease_gaia_lemonade", lambda *a: None)
    monkeypatch.setattr(EmbeddedLemonade, "is_installed", lambda self: True)
    LemonadeManager.reset()
    yield w
    LemonadeManager.reset()


def _client():
    return lc.LemonadeClient(model=MODEL, base_url=lc.resolve_lemonade_base_url())


def _assert_ensure_shape(post):
    """The daemon boundary: the real client's request, as the route expects it."""
    assert post["url"] == ENSURE_URL
    assert post["headers"] == {"Authorization": f"Bearer {DAEMON.token}"}
    assert post["json"] is None
    assert post["timeout"] == (5.0, 80.0)


class TestTheRace:
    def test_a_quiet_server_the_daemon_restarts_is_waited_for(self, world):
        """Unreachable on the first probe; the daemon restarts it moments later."""
        world.daemon_answers = [world.not_answering, world.restarted]
        client = _client()
        statuses = []

        assert client.wait_for_gaia_lemonade(on_status=statuses.append) is True

        assert statuses == [lc.RECONNECTING_MESSAGE]
        assert client.base_url == f"http://localhost:{NEW_PORT}/api/v1"
        assert client.api_key == NEW_KEY
        assert len(world.daemon_posts) == 2
        for post in world.daemon_posts:
            _assert_ensure_shape(post)
        assert 0 < world.now <= lc.GAIA_LEMONADE_RECONNECT_SECONDS

    def test_a_server_that_answers_again_on_its_port_is_kept(self, world):
        world.daemon_answers = [
            world.not_answering,
            world.not_answering,
            world.came_back,
        ]
        client = _client()

        assert client.wait_for_gaia_lemonade() is True

        assert client.base_url == f"http://localhost:{OLD_PORT}/api/v1"
        assert client.api_key == OLD_KEY

    def test_the_first_request_waits_and_is_sent_once_more(self, world):
        world.daemon_answers = [world.not_answering, world.restarted]
        client = _client()
        phases = []
        client.model_load_listener = lambda model, state: phases.append((model, state))

        result = client.chat_completions(
            MODEL, [{"role": "user", "content": "hi"}], auto_download=False
        )

        assert result["choices"][0]["message"]["content"] == "hi"
        assert phases == [(MODEL, "reconnecting")]
        assert world.lemonade_posts == [
            (
                f"http://localhost:{NEW_PORT}/api/v1/chat/completions",
                f"Bearer {NEW_KEY}",
            )
        ]

    def test_a_server_already_answering_is_not_waited_for(self, world):
        world.listening = {OLD_PORT}
        statuses = []

        assert _client().wait_for_gaia_lemonade(on_status=statuses.append) is False
        assert statuses == []
        assert world.daemon_posts == []

    def test_a_server_restarted_since_the_failure_is_followed_and_reported(self, world):
        """The caller's request failed on the old port; the daemon already moved it."""
        client = _client()
        world.restarted()

        assert client.wait_for_gaia_lemonade() is True
        assert client.base_url == f"http://localhost:{NEW_PORT}/api/v1"
        assert world.daemon_posts == []


class TestBackToBackSessions:
    def test_sixteen_sessions_each_meeting_a_restart_all_answer(self, world):
        """Each new session finds the server quiet for a different while."""
        failures = []
        for session in range(16):
            port, key = 61000 + session, f"key-{session}"
            world.live_pids = {session + 10}
            world.listening = set()
            world.record(pid=session + 10, port=port, key=key)
            client = _client()
            quiet = [world.not_answering] * (session % 4)

            def _restarted(port=port + 100, key=f"{key}-new", pid=session + 100):
                world.live_pids = {pid}
                world.listening = {port}
                world.record(pid=pid, port=port, key=key)
                return _Http(200, {"base_url": f"http://localhost:{port}/api/v1"})

            world.daemon_answers = quiet + [_restarted]
            world.lemonade_posts.clear()
            try:
                client.chat_completions(
                    MODEL, [{"role": "user", "content": "hi"}], auto_download=False
                )
            except lc.LemonadeClientError as e:
                failures.append((session, str(e)))
            assert world.lemonade_posts == [
                (
                    f"http://localhost:{port + 100}/api/v1/chat/completions",
                    f"Bearer {key}-new",
                )
            ]

        assert failures == []


class TestItFailsLoudlyWithinTheBound:
    def test_a_server_that_never_returns_names_the_cause_and_the_fix(self, world):
        world.daemon_answers = [world.not_answering]
        client = _client()

        with pytest.raises(lc.LemonadeClientError) as raised:
            client.wait_for_gaia_lemonade()

        message = str(raised.value)
        assert (
            f"was not back within {lc.GAIA_LEMONADE_RECONNECT_SECONDS:.0f}s" in message
        )
        assert "running but not answering" in message
        assert "gaia daemon status" in message
        assert "daemon log" in message
        assert world.now == pytest.approx(lc.GAIA_LEMONADE_RECONNECT_SECONDS)
        # Bounded: one ask per poll, never a tight loop.
        polls = (
            lc.GAIA_LEMONADE_RECONNECT_SECONDS / lc.GAIA_LEMONADE_RECONNECT_POLL_SECONDS
        )
        assert len(world.daemon_posts) <= polls + 1

    def test_the_turn_fails_with_that_error_not_a_bare_refusal(self, world):
        world.daemon_answers = [world.not_answering]
        with pytest.raises(lc.LemonadeClientError, match="was not back within"):
            _client().chat_completions(
                MODEL, [{"role": "user", "content": "hi"}], auto_download=False
            )

    def test_a_daemon_that_cannot_start_it_fails_at_once(self, world):
        """A definite failure is not retried: no hidden retry loop."""
        world.live_pids = set()  # the quiet server died
        world.daemon_answers = [world.cannot_start]

        with pytest.raises(lc.LemonadeClientError, match="read lemond.log"):
            _client().wait_for_gaia_lemonade()

        assert len(world.daemon_posts) == 1
        assert world.now == 0.0

    def test_a_custom_bound_is_honoured(self, world):
        world.daemon_answers = [world.not_answering]
        with pytest.raises(lc.LemonadeClientError, match="within 5s"):
            _client().wait_for_gaia_lemonade(timeout=5.0)
        assert world.now == pytest.approx(5.0)


@pytest.mark.embedded_start
class TestANewSessionWaitsToo:
    """Startup (``ensure_ready``) meets the same gap a running session does."""

    @pytest.fixture
    def startup(self, world, monkeypatch):
        monkeypatch.setattr(
            EmbeddedLemonade,
            "status",
            lambda self: EmbeddedStatus(installed=True, running=False, version="v"),
        )
        monkeypatch.setattr(
            lc.LemonadeClient,
            "get_status",
            lambda self: lc.LemonadeStatus(running=True, url=self.base_url),
        )
        return world

    def test_a_session_opened_in_the_gap_waits_and_starts(self, startup, capsys):
        startup.daemon_answers = [startup.not_answering, startup.restarted]

        assert LemonadeManager.ensure_ready(quiet=False, preload=False)

        assert lc.RECONNECTING_MESSAGE in capsys.readouterr().err
        assert len(startup.daemon_posts) >= 2
        for post in startup.daemon_posts:
            _assert_ensure_shape(post)

    def test_a_session_whose_server_never_returns_fails_loudly(self, startup, capsys):
        startup.daemon_answers = [startup.not_answering]

        assert not LemonadeManager.ensure_ready(quiet=False, preload=False)

        err = capsys.readouterr().err
        assert "was not back within" in err
        assert "gaia daemon status" in err
        assert startup.now == pytest.approx(lc.GAIA_LEMONADE_RECONNECT_SECONDS)

    def test_a_failed_start_is_still_reported_at_once(self, startup, capsys):
        startup.daemon_answers = [startup.cannot_start]

        assert not LemonadeManager.ensure_ready(quiet=False, preload=False)

        assert "Could not start GAIA's Lemonade Server" in capsys.readouterr().err
        assert len(startup.daemon_posts) == 1
        assert startup.now == 0.0


class TestNotGaiasServer:
    def test_a_users_own_server_is_never_waited_on_or_restarted(
        self, world, monkeypatch
    ):
        monkeypatch.setenv("LEMONADE_BASE_URL", f"http://localhost:{OLD_PORT}")
        world.daemon_answers = [world.restarted]
        client = _client()
        statuses = []

        assert client.follows_gaia_lemonade is False
        assert client.wait_for_gaia_lemonade(on_status=statuses.append) is False
        with pytest.raises(requests.exceptions.ConnectionError):
            client.chat_completions(
                MODEL, [{"role": "user", "content": "hi"}], auto_download=False
            )
        assert world.daemon_posts == []
        assert statuses == []
        assert world.now == 0.0

    def test_a_remote_server_is_never_waited_on_or_restarted(self, world, monkeypatch):
        monkeypatch.setenv("LEMONADE_BASE_URL", "http://gpu-box.lan:13305")
        world.daemon_answers = [world.restarted]
        client = _client()

        assert client.wait_for_gaia_lemonade() is False
        assert world.daemon_posts == []

    def test_the_saved_external_choice_is_never_waited_on(self, world, monkeypatch):
        monkeypatch.setattr(
            "gaia.llm.lemonade_choice.external_lemonade_url",
            lambda: f"http://localhost:{OLD_PORT}",
        )
        world.daemon_answers = [world.restarted]

        assert _client().wait_for_gaia_lemonade() is False
        assert world.daemon_posts == []


class TestTheStalePortNeverRestartsALeasedServer:
    """A client built while no server was recorded holds the default port."""

    def test_a_default_port_client_follows_the_running_server_without_the_daemon(
        self, world
    ):
        world.embedded.state_path.unlink()
        client = _client()
        assert client.base_url == lc.DEFAULT_LEMONADE_URL
        assert client.follows_gaia_lemonade

        # The daemon has since started GAIA's server, which a session is using.
        world.live_pids = {2}
        world.listening = {NEW_PORT}
        world.record(pid=2, port=NEW_PORT, key=NEW_KEY)
        world.daemon_answers = [world.cannot_start]

        assert client.current_base_url() == f"http://localhost:{NEW_PORT}/api/v1"
        assert client.wait_for_gaia_lemonade() is False
        assert world.daemon_posts == []

    def test_a_stale_port_never_reaches_a_stop(self, world, monkeypatch):
        """Even waiting on a default-port client only ever asks ``ensure``."""
        world.embedded.state_path.unlink()
        client = _client()
        world.daemon_answers = [world.not_answering, world.restarted]
        stopped = []
        monkeypatch.setattr(
            EmbeddedLemonade, "stop", lambda self, *a, **k: stopped.append(1)
        )

        client.wait_for_gaia_lemonade()

        assert stopped == []
        assert {p["url"] for p in world.daemon_posts} == {ENSURE_URL}


class _Embedded:
    """GAIA's server as ``EmbeddedLemonadeOwner`` sees it."""

    version = "1"

    def __init__(self, current: EmbeddedStatus):
        self.current = current
        self.started = []
        self.stopped = []

    def is_installed(self):
        return True

    def status(self):
        return self.current

    def start(self, **kwargs):
        self.started.append(kwargs)
        raise AssertionError("a running server must never be started again")

    def stop(self, *a, **k):
        self.stopped.append(1)
        raise AssertionError("a leased server must never be stopped")

    def claim(self, *a):
        pass

    log_path = "lemond.log"


def _owner(current):
    from gaia.daemon.lemonade import EmbeddedLemonadeOwner

    embedded = _Embedded(current)
    owner = EmbeddedLemonadeOwner(
        factory=lambda: embedded, owner_pid=7, owner_alive=lambda pid, t: True
    )
    owner.configured_url = staticmethod(lambda: None)
    return owner, embedded


class TestTheDaemonNeverRestartsAServerInUse:
    def test_ensure_returns_the_running_server_untouched(self):
        owner, embedded = _owner(
            EmbeddedStatus(
                installed=True,
                running=True,
                version="1",
                port=NEW_PORT,
                pid=2,
                base_url=f"http://localhost:{NEW_PORT}/api/v1",
                owner_pid=7,
            )
        )

        served = owner.ensure()

        assert (served.port, served.started) == (NEW_PORT, False)
        assert embedded.started == [] and embedded.stopped == []

    def test_a_quiet_server_is_reported_not_answering_and_left_alone(self):
        owner, embedded = _owner(
            EmbeddedStatus(
                installed=True,
                running=False,
                version="1",
                port=OLD_PORT,
                unresponsive_pid=1,
                owner_pid=7,
            )
        )

        with pytest.raises(EmbeddedLemonadeNotAnswering, match="running but stuck"):
            owner.ensure()
        assert embedded.started == [] and embedded.stopped == []


@pytest.mark.allow_network
class TestTheEnsureRoute:
    """The route marks only the not-answering case as worth waiting for."""

    def _post(self, error):
        from fastapi.testclient import TestClient

        from gaia.daemon.app import create_app

        class _Owner:
            def ensure(self):
                raise error

        app = create_app(
            token="tok", port=55124, pid=4343, started_at=0.0, lemonade_owner=_Owner()
        )
        return TestClient(app).post(
            "/daemon/v1/lemonade/ensure", headers={"Authorization": "Bearer tok"}
        )

    def test_not_answering_is_a_503_with_retry_after(self):
        resp = self._post(EmbeddedLemonadeNotAnswering(NOT_ANSWERING))
        assert resp.status_code == 503
        assert resp.headers["Retry-After"] == "2"
        assert resp.json()["detail"] == NOT_ANSWERING

    def test_a_failed_start_is_a_503_without_one(self):
        resp = self._post(EmbeddedLemonadeError("lemond exited; read lemond.log"))
        assert resp.status_code == 503
        assert "Retry-After" not in resp.headers

    def test_the_client_maps_each_to_its_own_error(self, monkeypatch):
        from gaia.daemon import client as daemon_client
        from gaia.daemon.errors import DaemonError, LemonadeNotAnsweringError

        monkeypatch.setattr(daemon_client, "start_or_attach", lambda: DAEMON)
        answers = [
            self._post(EmbeddedLemonadeNotAnswering(NOT_ANSWERING)),
            self._post(EmbeddedLemonadeError("lemond exited; read lemond.log")),
        ]
        monkeypatch.setattr(requests, "post", lambda *a, **k: answers.pop(0))

        with pytest.raises(LemonadeNotAnsweringError, match="not answering"):
            daemon_client.ensure_lemonade()
        with pytest.raises(DaemonError, match="read lemond.log") as raised:
            daemon_client.ensure_lemonade()
        assert not isinstance(raised.value, LemonadeNotAnsweringError)
