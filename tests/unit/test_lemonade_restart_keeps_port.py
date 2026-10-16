# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""After GAIA's Lemonade stops, the next start lands on GAIA's port, never 13305.

Once the server is stopped its state file is gone, so the address a client
resolves falls back to the default port. On one lab host another Lemonade sat
there and GAIA silently used it; on another nothing did, and the daemon's
``/lemonade/start`` answered a bare 500. Every test here runs the real owner,
supervisor, route and :class:`EmbeddedLemonade` port choice; only lemond itself
(``Popen`` and its health check) is faked, and a real HTTP server plays the
stranger on the default port.
"""

import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace

import pytest

import gaia.llm.lemonade_embedded as embedded_mod
from gaia.daemon.lemonade import EmbeddedLemonadeOwner
from gaia.llm import lemonade_client as lc
from gaia.llm import lemonade_service
from gaia.llm.lemonade_embedded import EmbeddedLemonade
from gaia.llm.lemonade_supervisor import LemonadeStartError, LemonadeSupervisor

# Real sockets: the stranger's HTTP server, the port probe, FastAPI's TestClient.
pytestmark = pytest.mark.allow_network

TOKEN = "daemon-token"


class Stranger:
    """Another Lemonade on the default port, recording every request it gets."""

    def __init__(self):
        self.requests = []
        stranger = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass

            def do_GET(self):
                stranger.requests.append(self.path)
                body = json.dumps({"status": "ok"}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            do_POST = do_GET

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()


class Lab:
    """GAIA's installed server, its daemon's owner and supervisor."""

    def __init__(self, embedded: EmbeddedLemonade):
        self.embedded = embedded
        self.spawned_ports = []
        self.owner = EmbeddedLemonadeOwner(factory=lambda: embedded, owner_pid=1)
        self.supervisor = LemonadeSupervisor(embedded=self.owner)

    def stop(self):
        """What an idle stop or `gaia lemonade embedded stop` leaves behind:
        no state file, no credentials, the last port still on record."""
        self.embedded._clear_state()

    @property
    def last_port(self):
        return json.loads(self.embedded.last_port_path.read_text())["port"]


@pytest.fixture
def lab(tmp_path, monkeypatch):
    monkeypatch.setenv("GAIA_HOME", str(tmp_path / "gaia-home"))
    for var in ("LEMONADE_BASE_URL", "LEMONADE_API_KEY", "GAIA_LEMONADE_EMBEDDED"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(EmbeddedLemonade, "is_installed", lambda self: True)
    monkeypatch.setattr(EmbeddedLemonade, "write_config", lambda self: None)
    embedded = EmbeddedLemonade()
    lab = Lab(embedded)

    def health(self, port, key, timeout=3.0):
        state = self._read_state()
        live = state and state.get("port") == port and state.get("api_key") == key
        return {"status": "ok", "all_models_loaded": []} if live else None

    def popen(argv, **kwargs):
        lab.spawned_ports.append(int(argv[argv.index("--port") + 1]))
        # This process's pid, so pid checks see a live server.
        return SimpleNamespace(pid=os.getpid(), poll=lambda: None, returncode=None)

    monkeypatch.setattr(EmbeddedLemonade, "_health", health)
    monkeypatch.setattr(embedded_mod.subprocess, "Popen", popen)
    yield lab
    lemonade_service.install_supervisor(None)


@pytest.fixture
def stranger(monkeypatch):
    """Another Lemonade answering where a stopped GAIA server's address falls back."""
    server = Stranger()
    url = f"http://localhost:{server.port}/api/v1"
    monkeypatch.setattr(lc, "DEFAULT_LEMONADE_URL", url)
    monkeypatch.setattr(lc, "DEFAULT_PORT", server.port)
    yield server
    server.close()


@pytest.fixture
def no_stranger(monkeypatch):
    """Nothing at all listens where a stopped GAIA server's address falls back."""
    import socket

    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    monkeypatch.setattr(lc, "DEFAULT_LEMONADE_URL", f"http://localhost:{port}/api/v1")
    monkeypatch.setattr(lc, "DEFAULT_PORT", port)
    return port


def _route_through_daemon(lab, monkeypatch):
    """Make ``_post_start`` reach the real ``/lemonade/start`` route."""
    import time

    import requests
    from fastapi.testclient import TestClient

    from gaia.daemon.app import create_app

    app = create_app(
        token=TOKEN,
        port=1,
        pid=1,
        started_at=time.time(),
        lemonade=lab.supervisor,
        lemonade_owner=lab.owner,
    )
    http = TestClient(app)
    responses = []

    def attach(*args, **kwargs):
        return SimpleNamespace(host="testserver", port=80, token=TOKEN)

    def post(url, json=None, headers=None, timeout=None):
        response = http.post(
            url.split("testserver:80", 1)[1], json=json, headers=headers
        )
        responses.append(response)
        return response

    monkeypatch.setattr("gaia.daemon.client.attach", attach)
    monkeypatch.setattr(requests, "post", post)
    return responses


def _first_start(lab):
    """The server `gaia init` left running, then stopped by the idle timeout."""
    first = lab.owner.ensure()
    assert first.started and first.port == lab.last_port
    lab.stop()
    assert lc.running_embedded_lemonade_url() is None
    return first.port


class TestRestartKeepsGaiasPort:
    def test_a_client_restart_ignores_the_lemonade_on_the_default_port(
        self, lab, stranger, monkeypatch
    ):
        responses = _route_through_daemon(lab, monkeypatch)
        port = _first_start(lab)

        state = lemonade_service.ensure_lemonade_running()

        assert state.base_url == f"http://localhost:{port}/api/v1"
        assert state.started is True
        assert lab.spawned_ports == [port, port]
        assert stranger.requests == [], "GAIA talked to someone else's server"
        assert [r.status_code for r in responses] == [200]

    def test_a_restart_with_nothing_on_the_default_port_is_not_a_500(
        self, lab, no_stranger, monkeypatch
    ):
        responses = _route_through_daemon(lab, monkeypatch)
        port = _first_start(lab)

        state = lemonade_service.ensure_lemonade_running()

        assert [r.status_code for r in responses] == [200]
        body = responses[0].json()
        assert body["base_url"] == f"http://localhost:{port}/api/v1"
        assert body["supervised"] is True and body["pid"] == os.getpid()
        assert state.base_url == body["base_url"]
        assert lab.spawned_ports == [port, port]

    def test_inside_the_daemon_the_restart_keeps_the_port(self, lab, stranger):
        port = _first_start(lab)
        lemonade_service.install_supervisor(lab.supervisor)

        state = lemonade_service.ensure_lemonade_running()

        assert state.base_url == f"http://localhost:{port}/api/v1"
        assert state.owned is True and state.pid == os.getpid()
        assert stranger.requests == []

    def test_a_running_server_is_returned_without_a_restart(
        self, lab, stranger, monkeypatch
    ):
        responses = _route_through_daemon(lab, monkeypatch)
        running = lab.owner.ensure()
        monkeypatch.setattr(
            lemonade_service, "_probe", lambda client: client.port == running.port
        )

        state = lemonade_service.ensure_lemonade_running()

        assert state.base_url == running.base_url and state.started is False
        assert responses == [] and lab.spawned_ports == [running.port]

    def test_a_client_followed_through_a_transparent_restart_keeps_the_port(
        self, lab, stranger, monkeypatch
    ):
        port = _first_start(lab)
        monkeypatch.setattr(
            "gaia.daemon.client.ensure_lemonade",
            lambda *a, **k: lab.owner.ensure().to_dict(),
        )
        client = lc.LemonadeClient(keep_alive=True, verbose=False)
        assert client.follows_gaia_lemonade

        assert client.current_base_url() == f"http://localhost:{port}/api/v1"
        assert (
            client.api_key == json.loads(lab.embedded.state_path.read_text())["api_key"]
        )
        assert stranger.requests == []

    def test_a_taken_last_port_moves_gaia_and_the_client_follows(
        self, lab, no_stranger, monkeypatch
    ):
        import socket

        responses = _route_through_daemon(lab, monkeypatch)
        port = _first_start(lab)
        squatter = socket.socket()
        try:
            squatter.bind(("127.0.0.1", port))
            squatter.listen(1)
            state = lemonade_service.ensure_lemonade_running()
        finally:
            squatter.close()

        assert state.base_url != f"http://localhost:{port}/api/v1"
        assert state.base_url == f"http://localhost:{lab.last_port}/api/v1"
        assert [r.status_code for r in responses] == [200]


class TestStartFailuresAreActionable:
    """A start that fails says why; it is never a bare Internal Server Error."""

    def _owner_raising(self, lab, monkeypatch, error):
        def ensure():
            raise error

        monkeypatch.setattr(lab.owner, "ensure", ensure)

    def test_a_saved_choice_error_is_a_503_with_its_message(self, lab, monkeypatch):
        from gaia.llm.lemonade_choice import LemonadeChoiceError

        responses = _route_through_daemon(lab, monkeypatch)
        self._owner_raising(
            lab, monkeypatch, LemonadeChoiceError("lemonade_base_url is not a URL")
        )

        with pytest.raises(LemonadeStartError, match="lemonade_base_url is not a URL"):
            lemonade_service.ensure_lemonade_running()
        assert responses[0].status_code == 503

    def test_an_unexpected_error_names_itself_and_where_to_look(self, lab, monkeypatch):
        responses = _route_through_daemon(lab, monkeypatch)
        self._owner_raising(lab, monkeypatch, AttributeError("started_pid"))

        with pytest.raises(LemonadeStartError) as raised:
            lemonade_service.ensure_lemonade_running()
        message = str(raised.value)
        assert "Internal Server Error" not in message
        assert "AttributeError: started_pid" in message
        assert "gaia daemon logs" in message
        assert responses[0].status_code == 500

    def test_a_daemon_answer_without_an_address_is_an_error(self, lab, monkeypatch):
        monkeypatch.setattr(
            lemonade_service, "_post_start", lambda payload, timeout: {"pid": 1}
        )
        lab.stop()

        with pytest.raises(LemonadeStartError, match="without saying where"):
            lemonade_service.ensure_lemonade_running()


class TestGaiaLlmAdvice:
    """`gaia llm`'s "is Lemonade there?" check never counts the stranger."""

    def test_a_stopped_gaia_server_is_down_whoever_answers_the_default_port(
        self, lab, stranger
    ):
        from gaia.cli import _lemonade_answers

        _first_start(lab)

        assert _lemonade_answers() is False
        assert stranger.requests == []

    def test_a_running_gaia_server_answers(self, lab, stranger, monkeypatch):
        from gaia.cli import _lemonade_answers

        running = lab.owner.ensure()
        probed = []
        monkeypatch.setattr(
            "gaia.llm.lemonade_supervisor._probe",
            lambda client: probed.append(client.port) or True,
        )

        assert _lemonade_answers() is True
        assert probed == [running.port]
