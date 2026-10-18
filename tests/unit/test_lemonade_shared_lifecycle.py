# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""GAIA's Lemonade lifecycle on a machine other clients share.

Each case runs against a real loopback server or a real child process, because
the bugs live in how sockets behave: a ``/health`` that answers slowly while a
model swap holds lemond's router lock, and a port another program takes between
GAIA choosing it and lemond binding it.
"""

from __future__ import annotations

import json
import logging
import os
import socket
import socketserver
import subprocess
import sys
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import psutil
import pytest

from gaia.llm import lemonade_embedded as emb
from gaia.llm import lemonade_supervisor as sup
from gaia.llm.lemonade_embedded import EmbeddedLemonade, EmbeddedLemonadeError
from gaia.llm.lemonade_supervisor import LemonadeSupervisor

pytestmark = pytest.mark.allow_network

KEY = "test-key"
# What a real lemond 2026.40.0 answered on /api/v1/docs while a model eviction
# held its router lock (and /health timed out): the supervisor's fingerprint.
_REAL_DOCS_INDEX = json.loads(
    (
        Path(__file__).parents[1] / "fixtures/lemonade/lemond_docs_index_2026.40.0.json"
    ).read_text(encoding="utf-8")
)
# Loopback only: never route the probe through a system proxy (macOS reads one).
_DIRECT = urllib.request.build_opener(urllib.request.ProxyHandler({}))


class _FakeLemond(ThreadingHTTPServer):
    """lemond's two probe routes: ``/health`` behind a lock, ``/live`` not."""

    daemon_threads = True

    def server_bind(self):
        # HTTPServer's own bind does a reverse DNS lookup that stalls on macOS.
        socketserver.TCPServer.server_bind(self)
        self.server_name, self.server_port = "127.0.0.1", self.server_address[1]

    def __init__(self, health_delay: float, lemonade: bool = True):
        self.health_delay = health_delay
        self.lemonade = lemonade
        super().__init__(("127.0.0.1", 0), _Handler)
        self.port = self.server_address[1]
        threading.Thread(target=self.serve_forever, daemon=True).start()


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):  # noqa: N802 — http.server's naming
        if self.path == "/live":
            body = {"status": "ok"}
        elif self.path == "/api/v1/docs":
            body = _REAL_DOCS_INDEX if self.server.lemonade else {"docs": []}
        elif self.path == "/api/v1/health":
            if self.headers.get("Authorization") != f"Bearer {KEY}":
                self.send_response(401)
                self.end_headers()
                return
            time.sleep(self.server.health_delay)
            body = {"status": "ok", "version": "x"}
        else:
            self.send_response(404)
            self.end_headers()
            return
        data = json.dumps(body).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


@pytest.fixture
def slow_health():
    """A lemond whose /health outlasts GAIA's 3 s probe, as one mid-swap does."""
    server = _FakeLemond(health_delay=4.0)
    yield server
    server.shutdown()
    server.server_close()


@pytest.fixture
def mute_listener():
    """A port that accepts connections and never answers HTTP: a stalled server."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    sock.listen(16)
    yield sock.getsockname()[1]
    sock.close()


@pytest.fixture
def manager(tmp_path, monkeypatch):
    m = EmbeddedLemonade(home=tmp_path)
    monkeypatch.setattr(m, "is_installed", lambda: True)
    monkeypatch.setattr(m, "write_config", lambda: None)
    return m


def _record_own_server(manager, port, monkeypatch):
    manager._write_state(
        {"pid": os.getpid(), "port": port, "api_key": KEY, "version": manager.version}
    )
    monkeypatch.setattr(EmbeddedLemonade, "_identify", lambda self, p, port, k: "own")


def _no_spawn(*args, **kwargs):
    pytest.fail("spawned a second Lemonade over one that is running")


class TestSlowButAliveEmbeddedServer:
    """C1: a server swapping models must not be called stalled, or stopped."""

    def test_a_busy_server_is_running_and_reused(
        self, manager, slow_health, monkeypatch
    ):
        _record_own_server(manager, slow_health.port, monkeypatch)
        monkeypatch.setattr(emb.subprocess, "Popen", _no_spawn)

        status = manager.status()
        assert status.running, "a busy server was reported as not running"
        assert status.busy
        assert status.unresponsive_pid is None
        assert status.base_url == manager.base_url_for(slow_health.port)

        # A second client's ensure gets the busy server back, not "run stop".
        started = manager.start()
        assert started.running and started.port == slow_health.port
        assert manager.state_path.exists()

    def test_a_server_answering_nothing_is_still_reported_stalled(
        self, manager, mute_listener, monkeypatch
    ):
        _record_own_server(manager, mute_listener, monkeypatch)
        monkeypatch.setattr(emb.subprocess, "Popen", _no_spawn)

        status = manager.status()
        assert not status.running
        assert status.unresponsive_pid == os.getpid()
        with pytest.raises(EmbeddedLemonadeError) as exc:
            manager.start()
        assert "not answering" in str(exc.value)
        assert "gaia lemonade embedded stop" in str(exc.value)

    def _recorded(self, manager, seconds_ago, activity):
        manager.busy_since_path.write_text(
            json.dumps(
                {
                    "pid": os.getpid(),
                    "quiet_since": time.time() - seconds_ago,
                    "activity": activity,
                }
            ),
            encoding="utf-8",
        )

    def _backends(self, monkeypatch, activity):
        monkeypatch.setattr(emb, "backend_activity", lambda pid: activity)

    def test_work_since_an_old_record_is_busy_not_stalled(
        self, manager, slow_health, monkeypatch
    ):
        """No caller polls status(), so the record may be hours old; what
        matters is whether the backends did anything since."""
        _record_own_server(manager, slow_health.port, monkeypatch)
        self._recorded(manager, 3 * 3600, {"111": 5.0})
        self._backends(monkeypatch, {"111": 9.0})

        status = manager.status()
        assert status.running and status.busy, "a working server was called stalled"

    def test_a_replaced_backend_counts_as_work(self, manager, slow_health, monkeypatch):
        """An eviction kills the old llama-server and the load starts a new one."""
        _record_own_server(manager, slow_health.port, monkeypatch)
        self._recorded(manager, 3 * 3600, {"111": 5.0})
        self._backends(monkeypatch, {"222": 0.0})

        assert manager.status().busy

    def test_a_backend_appearing_counts_as_work(
        self, manager, slow_health, monkeypatch
    ):
        _record_own_server(manager, slow_health.port, monkeypatch)
        self._recorded(manager, 3 * 3600, {})
        self._backends(monkeypatch, {"222": 0.0})

        assert manager.status().busy

    def test_the_first_quiet_sighting_is_only_recorded(
        self, manager, slow_health, monkeypatch
    ):
        """`gaia init` looks once; one sighting can prove nothing."""
        _record_own_server(manager, slow_health.port, monkeypatch)
        self._backends(monkeypatch, {"111": 5.0})

        assert manager.status().busy
        recorded = json.loads(manager.busy_since_path.read_text(encoding="utf-8"))
        assert recorded["activity"] == {"111": 5.0}

    def test_no_work_at_all_since_the_record_is_stalled(
        self, manager, slow_health, monkeypatch
    ):
        _record_own_server(manager, slow_health.port, monkeypatch)
        self._recorded(manager, emb._QUIET_SECONDS + 1, {"111": 5.0})
        self._backends(monkeypatch, {"111": 5.0})

        status = manager.status()
        assert not status.running
        assert status.unresponsive_pid == os.getpid()

    def test_a_lock_held_with_no_backends_is_stalled_too(
        self, manager, slow_health, monkeypatch
    ):
        """Nothing runs that could release it: lemond downloads and installs
        backends inside a load, which runs with the router lock released."""
        _record_own_server(manager, slow_health.port, monkeypatch)
        self._recorded(manager, emb._QUIET_SECONDS + 1, {})
        self._backends(monkeypatch, {})

        assert manager.status().unresponsive_pid == os.getpid()

    def test_no_work_within_the_quiet_window_is_still_busy(
        self, manager, slow_health, monkeypatch
    ):
        _record_own_server(manager, slow_health.port, monkeypatch)
        self._recorded(manager, emb._QUIET_SECONDS - 30, {"111": 5.0})
        self._backends(monkeypatch, {"111": 5.0})

        assert manager.status().busy

    def test_without_evidence_it_is_never_called_stalled(
        self, manager, slow_health, monkeypatch
    ):
        _record_own_server(manager, slow_health.port, monkeypatch)
        self._recorded(manager, 3 * 3600, {"111": 5.0})
        self._backends(monkeypatch, None)

        assert manager.status().busy

    def test_a_recovered_server_starts_fresh_at_its_next_swap(
        self, manager, slow_health, monkeypatch
    ):
        """busy -> healthy -> busy, as `gaia init` sees it: the record from the
        first swap must not make the second look idle since then."""
        _record_own_server(manager, slow_health.port, monkeypatch)
        self._recorded(manager, emb._QUIET_SECONDS + 1, {"111": 5.0})
        self._backends(monkeypatch, {"111": 5.0})

        slow_health.health_delay = 0.0
        assert manager.status().busy is False
        assert not manager.busy_since_path.exists()

        slow_health.health_delay = 4.0
        assert manager.status().busy, "the old record called a fresh swap stalled"

    def test_forgetting_the_server_forgets_its_record(self, manager):
        manager.root.mkdir(parents=True, exist_ok=True)
        manager.busy_since_path.write_text("{}", encoding="utf-8")
        manager._clear_state()
        assert not manager.busy_since_path.exists()

    def test_an_unreadable_backend_is_no_evidence(self, monkeypatch):
        """Dropping it would read as "no work" and call a working server stuck."""

        class Hidden:
            pid = 222

            def cpu_times(self):
                raise psutil.AccessDenied(pid=222)

        class Lemond:
            def __init__(self, pid):
                pass

            def children(self, recursive=False):
                return [Hidden()]

        monkeypatch.setattr(psutil, "Process", Lemond)
        assert emb.backend_activity(111) is None

    def test_activity_is_read_from_the_real_process_tree(self, tmp_path):
        """A working backend's counters move; an idle one's stay put."""
        script = tmp_path / "tree.py"
        lines = [
            "import subprocess, sys, time",
            "work = 'while True: pass' if sys.argv[1] == 'busy' else "
            "'import time; time.sleep(120)'",
            "child = subprocess.Popen([sys.executable, '-c', work])",
            "time.sleep(120)",
        ]
        script.write_text(
            "".join(line + chr(10) for line in lines),
            encoding="utf-8",
        )
        trees = {
            kind: subprocess.Popen([sys.executable, str(script), kind])
            for kind in ("busy", "idle")
        }
        try:
            time.sleep(3)  # let the children start
            before = {k: emb.backend_activity(t.pid) for k, t in trees.items()}
            time.sleep(1)
            after = {k: emb.backend_activity(t.pid) for k, t in trees.items()}
            assert before["busy"] and before["idle"]
            assert after["busy"] != before["busy"]
            assert after["idle"] == before["idle"]
        finally:
            for tree in trees.values():
                for child in psutil.Process(tree.pid).children(recursive=True):
                    child.kill()
                tree.kill()
                tree.wait(timeout=10)

    def test_liveness_alone_never_vouches_for_another_process(
        self, manager, slow_health, monkeypatch
    ):
        """The pid gate still decides: /live has no key, so it proves no identity."""
        manager._write_state(
            {"pid": 1, "port": slow_health.port, "api_key": KEY, "version": "x"}
        )
        monkeypatch.setattr(
            EmbeddedLemonade, "_identify", lambda self, p, port, k: "other"
        )
        assert manager.status().running is False
        assert not manager.state_path.exists()


class _SlowClient:
    """A LemonadeClient whose health probe times out, like one mid-swap."""

    api_key = None

    def __init__(self, port):
        self.host = "127.0.0.1"
        self.port = port
        self.base_url = f"http://127.0.0.1:{port}/api/v1"

    def health_check(self, timeout=None):
        raise TimeoutError("read timed out")


class TestSlowButAliveSupervisedServer:
    """C2: an open, slow port is a busy Lemonade, not a stranger to refuse."""

    @pytest.fixture
    def supervisor(self, tmp_path, monkeypatch):
        def make(port):
            from gaia.llm import lemonade_client

            monkeypatch.setattr(lemonade_client, "DEFAULT_PORT", port)
            monkeypatch.setattr(
                "gaia.llm.lemonade_choice.external_mode_active", lambda: False
            )
            monkeypatch.setattr(sup, "_OCCUPIED_PORT_GRACE_S", 0.3)
            monkeypatch.setattr(sup, "_PROBE_INTERVAL_S", 0.01)
            monkeypatch.setattr(
                LemonadeSupervisor,
                "_spawn",
                lambda self, spec: pytest.fail("spawned onto an occupied port"),
            )
            s = LemonadeSupervisor(log_dir=tmp_path)
            monkeypatch.setattr(s, "_client", lambda: _SlowClient(port))
            return s

        return make

    def test_a_busy_lemonade_is_attached_to(self, supervisor, slow_health, monkeypatch):
        s = supervisor(slow_health.port)
        # A busy Lemonade is known at once: no waiting out the stranger grace.
        monkeypatch.setattr(sup, "_OCCUPIED_PORT_GRACE_S", 60.0)
        started = time.monotonic()
        state = s.ensure_running(ctx_size=65536)
        assert time.monotonic() - started < 30, "waited out the grace window"
        assert state.started is False
        assert state.owned is False

    def test_the_listener_is_found_without_the_global_table(self, monkeypatch):
        """macOS refuses psutil.net_connections() without root; a server this
        user runs must still be found, by walking the user's own processes."""

        def refuse(*args, **kwargs):
            raise psutil.AccessDenied()

        monkeypatch.setattr(psutil, "net_connections", refuse)
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.bind(("127.0.0.1", 0))
        sock.listen(1)
        try:
            assert sup._listener_pid(sock.getsockname()[1]) == os.getpid()
        finally:
            sock.close()

    def test_a_quiet_busy_system_server_is_an_error(
        self, supervisor, slow_health, monkeypatch
    ):
        """A wedged system Lemonade still answers /live; once its backends have
        been quiet too long GAIA says so rather than attaching to it forever."""
        monkeypatch.setattr(sup, "_listener_pid", lambda port: os.getpid())
        monkeypatch.setattr(emb, "backend_activity", lambda pid: {"111": 5.0})
        s = supervisor(slow_health.port)
        assert s.ensure_running(ctx_size=65536).started is False
        (seen,) = s._busy_seen.values()
        seen["quiet_since"] -= emb._QUIET_SECONDS + 1
        with pytest.raises(sup.LemonadeStartError) as exc:
            s.ensure_running(ctx_size=65536)
        assert "stuck" in str(exc.value)

    def test_a_recovered_server_starts_fresh_at_its_next_swap(
        self, supervisor, slow_health, monkeypatch
    ):
        """busy -> healthy -> busy: the first swap's record must not carry over."""
        monkeypatch.setattr(sup, "_listener_pid", lambda port: os.getpid())
        monkeypatch.setattr(emb, "backend_activity", lambda pid: {"111": 5.0})
        s = supervisor(slow_health.port)
        assert s.ensure_running(ctx_size=65536).started is False
        (seen,) = s._busy_seen.values()
        seen["quiet_since"] -= emb._QUIET_SECONDS + 1

        healthy = _SlowClient(slow_health.port)
        healthy.health_check = lambda timeout=None: {"status": "ok"}
        monkeypatch.setattr(s, "_client", lambda: healthy)
        assert s.ensure_running(ctx_size=65536).started is False
        assert not s._busy_seen, "the recovered server kept its quiet record"

        monkeypatch.setattr(s, "_client", lambda: _SlowClient(slow_health.port))
        assert s.ensure_running(ctx_size=65536).started is False

    def test_a_system_server_it_cannot_inspect_is_never_called_stuck(
        self, supervisor, slow_health, monkeypatch
    ):
        """Another user's server hides its pid and sockets: no evidence, no verdict."""
        monkeypatch.setattr(sup, "_listener_pid", lambda port: None)
        s = supervisor(slow_health.port)
        assert s.ensure_running(ctx_size=65536).started is False
        assert s.ensure_running(ctx_size=65536).started is False

    def test_another_app_answering_live_and_docs_is_not_taken_for_lemonade(
        self, supervisor
    ):
        stranger = _FakeLemond(health_delay=4.0, lemonade=False)
        try:
            with pytest.raises(sup.LemonadeStartError):
                supervisor(stranger.port).ensure_running(ctx_size=65536)
        finally:
            stranger.shutdown()
            stranger.server_close()

    def test_a_stranger_on_the_port_is_still_refused(self, supervisor, mute_listener):
        with pytest.raises(sup.LemonadeStartError) as exc:
            supervisor(mute_listener).ensure_running(ctx_size=65536)
        assert str(mute_listener) in str(exc.value)


# lemond's own bind rule: it exits 1 when either loopback address is taken.
_FAKE_LEMOND = r"""
import json, socket, socketserver, sys, threading
from http.server import BaseHTTPRequestHandler, HTTPServer
port = int(sys.argv[sys.argv.index("--port") + 1])
class Server(HTTPServer):
    allow_reuse_address = False
    def server_bind(self):  # skip HTTPServer's reverse DNS lookup
        socketserver.TCPServer.server_bind(self)
        self.server_name, self.server_port = "127.0.0.1", self.server_address[1]
class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def do_GET(self):
        data = json.dumps({"status": "ok"}).encode()
        self.send_response(200); self.send_header("Content-Length", str(len(data)))
        self.end_headers(); self.wfile.write(data)
try:
    server = Server(("127.0.0.1", port), H)
except OSError:
    print(f"Port {port} on 127.0.0.1 is already in use", flush=True)
    sys.exit(1)
server.serve_forever()
"""


class TestPortTakenBeforeLemondBinds:
    """C3: another program can take the chosen port before lemond binds it."""

    @pytest.fixture
    def fake_lemond(self, manager, tmp_path, monkeypatch):
        script = tmp_path / "fake_lemond.py"
        script.write_text(_FAKE_LEMOND, encoding="utf-8")
        manager.dist_dir.mkdir(parents=True, exist_ok=True)
        spawned = []
        real_popen = subprocess.Popen

        def popen(argv, **kwargs):
            if Path(argv[0]).stem != "lemond":
                return real_popen(argv, **kwargs)  # the failed start's cleanup
            kwargs.pop("env", None)
            proc = real_popen([sys.executable, str(script), *argv[1:]], **kwargs)
            spawned.append(proc)
            return proc

        monkeypatch.setattr(emb.subprocess, "Popen", popen)

        def healthy(port, api_key, timeout=None):
            # An HTTP answer, as the real probe needs: a thief's bare socket
            # accepts the connection but never replies.
            try:
                with _DIRECT.open(f"http://127.0.0.1:{port}/", timeout=1):
                    return {"ok": True}
            except OSError:
                return None

        monkeypatch.setattr(manager, "_health", healthy)
        yield spawned
        for proc in spawned:
            if proc.poll() is None:
                proc.kill()
                proc.wait(timeout=10)

    @pytest.fixture
    def thieves(self):
        held = []

        def take(port=0):
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.bind(("127.0.0.1", port))
            sock.listen(1)
            held.append(sock)
            return sock.getsockname()[1]

        yield take
        for sock in held:
            sock.close()

    def _choose_then_lose(self, manager, monkeypatch, take):
        """Pick a free port, then let another program bind it first."""
        real_choose = manager._choose_port
        lost = []

        def choose():
            port = real_choose()
            take(port)
            lost.append(port)
            return port

        monkeypatch.setattr(manager, "_choose_port", choose)
        return lost

    def test_a_lost_port_is_retried_on_a_new_one(
        self, manager, fake_lemond, thieves, monkeypatch, caplog
    ):
        lost = self._choose_then_lose(manager, monkeypatch, thieves)

        with caplog.at_level(logging.WARNING, logger=emb.log.name):
            status = manager.start(timeout=30)

        assert status.running and status.port != lost[0]
        assert len(fake_lemond) == 2
        assert any(
            f"Port {lost[0]} was taken" in r.getMessage() for r in caplog.records
        ), "the retry was silent"
        assert json.loads(manager.last_port_path.read_text())["port"] == status.port

    def test_retries_are_bounded(self, manager, fake_lemond, thieves, monkeypatch):
        # Every port GAIA picks, the first chosen included, is taken first.
        monkeypatch.setattr(emb, "_free_port", thieves)

        with pytest.raises(EmbeddedLemonadeError) as exc:
            manager.start(timeout=30)

        assert "taken by another program" in str(exc.value)
        assert len(fake_lemond) == emb._PORT_ATTEMPTS
        assert not manager.state_path.exists()

    def test_a_port_the_caller_named_is_never_swapped(
        self, manager, fake_lemond, thieves
    ):
        port = thieves()
        with pytest.raises(EmbeddedLemonadeError) as exc:
            manager.start(port=port, timeout=30)
        assert len(fake_lemond) == 1
        assert str(port) in str(exc.value)
        assert "is in use by another program" in str(exc.value)


def test_fake_lemond_matches_the_real_bind_rule(tmp_path):
    """Guards the fake: lemond exits 1 when its port is held (seen on 2026.40.0)."""
    script = tmp_path / "fake_lemond.py"
    script.write_text(_FAKE_LEMOND, encoding="utf-8")
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    sock.listen(1)
    try:
        proc = subprocess.run(
            [sys.executable, str(script), "--port", str(sock.getsockname()[1])],
            capture_output=True,
            timeout=30,
            check=False,
        )
    finally:
        sock.close()
    assert proc.returncode == 1
