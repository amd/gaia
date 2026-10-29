# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""GAIA kills only its own Lemonade, keeps its port, and frees its models.

Adversarial on purpose: every kill path is driven with a process that is NOT
GAIA's server sitting where GAIA's used to be, and the tests assert the exact
signal targets on Windows and on Linux.
"""

import errno
import json
import signal
import socket
import sys
import threading
import time
import urllib.error
import urllib.request
from contextlib import contextmanager
from types import SimpleNamespace

import psutil
import pytest

import gaia.llm.lemonade_embedded as embedded_mod
from gaia.llm.lemonade_embedded import (
    OTHER,
    OWN,
    UNKNOWN,
    EmbeddedLemonade,
    EmbeddedLemonadeError,
)

KEY = "recorded-key"
PORT = 50123
PID = 4321


@pytest.fixture
def manager(tmp_path):
    return EmbeddedLemonade(home=tmp_path)


def _own_exe(manager, version=None):
    name = "lemond.exe" if sys.platform == "win32" else "lemond"
    return str(manager.root / "dist" / (version or manager.version) / name)


class _FakeProcess:
    """psutil.Process stand-in: a cmdline, an environment, maybe refusals."""

    def __init__(
        self, cmdline, env=None, cmdline_denied=False, env_denied=False, name=None
    ):
        self._cmdline = cmdline
        self._env = env or {}
        self._cmdline_denied = cmdline_denied
        self._env_denied = env_denied
        self._name = name

    def name(self):
        if self._name is None:
            raise psutil.AccessDenied(PID)
        return self._name

    def cmdline(self):
        if self._cmdline_denied:
            raise psutil.AccessDenied(PID)
        return self._cmdline

    def environ(self):
        if self._env_denied:
            raise psutil.AccessDenied(PID)
        return self._env


def _processes(monkeypatch, table):
    """Make psutil.Process(pid) answer from *table*; absent pids do not exist."""

    def _process(pid):
        if pid not in table:
            raise psutil.NoSuchProcess(pid)
        return table[pid]

    monkeypatch.setattr(psutil, "Process", _process)


def _record(manager, pid=PID, port=PORT, owner_pid=None):
    manager._write_state(
        {
            "pid": pid,
            "port": port,
            "api_key": KEY,
            "version": manager.version,
            "owner_pid": owner_pid,
        }
    )


class TestIdentity:
    """`_identify` is the gate in front of every kill."""

    def test_gaias_own_lemond_is_recognised(self, manager, monkeypatch):
        _processes(
            monkeypatch,
            {
                PID: _FakeProcess(
                    [_own_exe(manager), "cache", "config", "--port", str(PORT)],
                    {"LEMONADE_API_KEY": KEY},
                )
            },
        )
        assert manager._identify(PID, PORT, KEY) == OWN

    def test_an_older_gaia_version_is_still_gaias(self, manager, monkeypatch):
        """An orphan from before an upgrade runs out of another dist/ version."""
        _processes(
            monkeypatch,
            {
                PID: _FakeProcess(
                    [_own_exe(manager, "0.0.1"), "c", "c", "--port", str(PORT)],
                    {"LEMONADE_API_KEY": KEY},
                )
            },
        )
        assert manager._identify(PID, PORT, KEY) == OWN

    @pytest.mark.parametrize(
        "cmdline,env",
        [
            # The pid was reused by an unrelated program.
            (["/usr/bin/python3", "-m", "http.server", str(PORT)], {}),
            # The user's own lemond, on the recorded port and pid.
            (["/usr/bin/lemond", "--port", str(PORT)], {"LEMONADE_API_KEY": KEY}),
            # GAIA's binary, but not the instance the record describes.
            (None, {"LEMONADE_API_KEY": KEY}),
            # Right binary and port, but another key: someone else launched it.
            ("own", {"LEMONADE_API_KEY": "someone-elses"}),
        ],
        ids=["pid-reused", "user-lemond", "other-port", "other-key"],
    )
    def test_anything_else_is_not_gaias(self, manager, monkeypatch, cmdline, env):
        if cmdline is None:
            cmdline = [_own_exe(manager), "c", "c", "--port", str(PORT + 1)]
        elif cmdline == "own":
            cmdline = [_own_exe(manager), "c", "c", "--port", str(PORT)]
        _processes(monkeypatch, {PID: _FakeProcess(cmdline, env)})
        assert manager._identify(PID, PORT, KEY) == OTHER

    @pytest.mark.parametrize(
        "name,expected",
        [("lemond.exe", UNKNOWN), ("lemond", UNKNOWN), (None, UNKNOWN)],
        ids=["windows", "posix", "name-hidden-too"],
    )
    def test_a_lemond_gaia_may_not_inspect_is_unknown_not_foreign(
        self, manager, monkeypatch, name, expected
    ):
        """Started from an admin terminal: neither killed nor forgotten."""
        _processes(
            monkeypatch,
            {PID: _FakeProcess([], cmdline_denied=True, name=name)},
        )
        assert manager._identify(PID, PORT, KEY) == expected

    def test_another_program_gaia_may_not_inspect_is_foreign(
        self, manager, monkeypatch
    ):
        _processes(
            monkeypatch,
            {PID: _FakeProcess([], cmdline_denied=True, name="svchost.exe")},
        )
        assert manager._identify(PID, PORT, KEY) == OTHER

    def test_an_uninspectable_stalled_server_keeps_its_record(
        self, manager, monkeypatch
    ):
        """Forgetting it would leak it and let the next start spawn a second."""
        _record(manager)
        _processes(
            monkeypatch,
            {PID: _FakeProcess([], cmdline_denied=True, name="lemond.exe")},
        )
        monkeypatch.setattr(EmbeddedLemonade, "_health", lambda *a, **k: None)
        killed = []
        monkeypatch.setattr(EmbeddedLemonade, "_kill_tree", staticmethod(killed.append))

        assert manager.status().unresponsive_pid == PID
        with pytest.raises(EmbeddedLemonadeError, match="administrator terminal"):
            manager.stop()
        assert killed == []
        assert manager.state_path.exists()

    def test_a_hidden_environment_leaves_the_binary_and_port_to_decide(
        self, manager, monkeypatch
    ):
        _processes(
            monkeypatch,
            {
                PID: _FakeProcess(
                    [_own_exe(manager), "c", "c", "--port", str(PORT)],
                    env_denied=True,
                )
            },
        )
        assert manager._identify(PID, PORT, KEY) == OWN

    def test_a_gone_process_is_not_gaias(self, manager, monkeypatch):
        _processes(monkeypatch, {})
        assert manager._identify(PID, PORT, KEY) == OTHER


class TestNeverKillsAStranger:
    def test_a_recycled_pid_is_forgotten_not_killed(self, manager, monkeypatch):
        """GAIA's server died; the OS gave its pid to the user's own lemond."""
        _record(manager)
        _processes(
            monkeypatch,
            {PID: _FakeProcess(["/usr/bin/lemond", "--port", "13305"])},
        )
        monkeypatch.setattr(EmbeddedLemonade, "_health", lambda *a, **k: None)
        killed = []
        monkeypatch.setattr(EmbeddedLemonade, "_kill_tree", staticmethod(killed.append))

        status = manager.status()
        assert status.running is False and status.unresponsive_pid is None
        assert manager.stop() is False
        assert killed == []
        assert not manager.state_path.exists()

    def test_a_server_answering_with_a_foreign_pid_is_refused(
        self, manager, monkeypatch
    ):
        """The key answers on the port, but the recorded pid is someone else's."""
        _record(manager)
        _processes(monkeypatch, {PID: _FakeProcess(["/usr/bin/vim"])})
        monkeypatch.setattr(EmbeddedLemonade, "_health", lambda *a, **k: {"ok": 1})
        killed = []
        monkeypatch.setattr(EmbeddedLemonade, "_kill_tree", staticmethod(killed.append))

        with pytest.raises(EmbeddedLemonadeError, match="killed nothing"):
            manager.stop()
        assert killed == []

    def test_gaias_own_server_is_killed_and_only_it(self, manager, monkeypatch):
        _record(manager)
        own = _FakeProcess(
            [_own_exe(manager), "c", "c", "--port", str(PORT)],
            {"LEMONADE_API_KEY": KEY},
        )
        user = _FakeProcess(["/usr/bin/lemond", "--port", "13305"])
        table = {PID: own, 13305: user}
        _processes(monkeypatch, table)
        monkeypatch.setattr(
            EmbeddedLemonade, "_health", lambda *a, **k: None if killed else {}
        )
        killed = []

        def _kill(pid):
            killed.append(pid)
            del table[pid]

        monkeypatch.setattr(EmbeddedLemonade, "_kill_tree", staticmethod(_kill))

        assert manager.stop() is True
        assert killed == [PID]
        assert 13305 in table, "the user's Lemonade on another port was touched"


class TestKillTargets:
    """The exact processes signalled, per OS."""

    def test_windows_kills_the_tree_of_the_recorded_pid(self, monkeypatch):
        calls = []
        monkeypatch.setattr(embedded_mod.platform, "system", lambda: "Windows")
        monkeypatch.setattr(
            embedded_mod.subprocess,
            "run",
            lambda argv, **kw: calls.append(argv),
        )
        EmbeddedLemonade._kill_tree(PID)
        assert calls == [["taskkill", "/PID", str(PID), "/T", "/F"]]

    def test_linux_kills_the_process_group_lemond_leads(self, monkeypatch):
        monkeypatch.setattr(embedded_mod.platform, "system", lambda: "Linux")
        monkeypatch.setattr(embedded_mod.os, "getpgid", lambda pid: pid, raising=False)
        groups, singles = [], []
        monkeypatch.setattr(
            embedded_mod.os,
            "killpg",
            lambda pgid, sig: groups.append((pgid, sig)),
            raising=False,
        )
        monkeypatch.setattr(
            embedded_mod.os, "kill", lambda pid, sig: singles.append((pid, sig))
        )
        EmbeddedLemonade._kill_tree(PID)
        assert groups == [(PID, signal.SIGTERM)]
        assert singles == []

    def test_linux_never_kills_a_group_lemond_does_not_lead(self, monkeypatch):
        """Killing the group would take its leader -- possibly GAIA itself."""
        monkeypatch.setattr(embedded_mod.platform, "system", lambda: "Linux")
        monkeypatch.setattr(embedded_mod.os, "getpgid", lambda pid: 77, raising=False)
        groups, singles = [], []
        monkeypatch.setattr(
            embedded_mod.os,
            "killpg",
            lambda pgid, sig: groups.append(pgid),
            raising=False,
        )
        monkeypatch.setattr(
            embedded_mod.os, "kill", lambda pid, sig: singles.append((pid, sig))
        )
        children = [SimpleNamespace(pid=5001), SimpleNamespace(pid=5002)]
        monkeypatch.setattr(
            psutil,
            "Process",
            lambda pid: SimpleNamespace(children=lambda recursive: children),
        )
        EmbeddedLemonade._kill_tree(PID)
        assert groups == []
        assert singles == [
            (5001, signal.SIGTERM),
            (5002, signal.SIGTERM),
            (PID, signal.SIGTERM),
        ]


_posix_only = pytest.mark.skipif(sys.platform == "win32", reason="POSIX socket rules")
_windows_only = pytest.mark.skipif(
    sys.platform != "win32", reason="Windows socket rules"
)


def _ipv6_loopback() -> bool:
    try:
        with socket.socket(socket.AF_INET6, socket.SOCK_STREAM) as s:
            s.bind(("::1", 0))
    except OSError:
        return False
    return True


_needs_ipv6 = pytest.mark.skipif(not _ipv6_loopback(), reason="no IPv6 loopback")


def _lemond_options(sock):
    """What cpp-httplib sets on lemond's listener."""
    opt = getattr(socket, "SO_REUSEPORT", socket.SO_REUSEADDR)
    sock.setsockopt(socket.SOL_SOCKET, opt, 1)


@contextmanager
def _listener(family, host, port=0, option=None):
    sock = socket.socket(family, socket.SOCK_STREAM)
    try:
        if option is not None:
            sock.setsockopt(socket.SOL_SOCKET, option, 1)
        sock.bind((host, port))
        sock.listen(8)
        yield sock.getsockname()[1]
    finally:
        sock.close()


def _leave_time_wait(port=0):
    """Run a lemond-like server on *port*, serve one connection, close it first.

    The side that closes first holds TIME_WAIT, so the port is left as a
    stopped lemond leaves it.
    """
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    _lemond_options(server)
    server.bind(("127.0.0.1", port))
    server.listen(1)
    port = server.getsockname()[1]
    client = socket.create_connection(("127.0.0.1", port))
    conn, _ = server.accept()
    conn.close()
    time.sleep(0.05)
    client.close()
    server.close()
    return port


@pytest.mark.allow_network
class TestStablePort:
    def _start(self, manager, monkeypatch, port=None):
        monkeypatch.setattr(manager, "is_installed", lambda: True)
        monkeypatch.setattr(manager, "write_config", lambda: None)
        monkeypatch.setattr(manager, "_health", lambda *a, **k: {"status": "ok"})
        argv_seen = []

        def fake_popen(argv, **kwargs):
            argv_seen.append(argv)
            return SimpleNamespace(pid=PID, poll=lambda: None, returncode=None)

        monkeypatch.setattr(embedded_mod.subprocess, "Popen", fake_popen)
        status = manager.start(port=port, owner_pid=1000)
        manager._clear_state()  # as if it was stopped
        return status, argv_seen[0]

    def test_the_last_port_is_reused_when_free(self, manager, monkeypatch):
        first, _ = self._start(manager, monkeypatch)
        second, argv = self._start(manager, monkeypatch)

        assert second.port == first.port
        assert argv[argv.index("--port") + 1] == str(first.port)
        assert json.loads(manager.last_port_path.read_text())["port"] == first.port

    def test_a_taken_port_moves_and_the_new_one_is_recorded(self, manager, monkeypatch):
        first, _ = self._start(manager, monkeypatch)
        squatter = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            squatter.bind(("127.0.0.1", first.port))
            squatter.listen(1)
            second, _ = self._start(manager, monkeypatch)
        finally:
            squatter.close()

        assert second.port != first.port
        assert json.loads(manager.last_port_path.read_text())["port"] == second.port

    def test_the_owner_is_recorded_with_the_instance(self, manager, monkeypatch):
        monkeypatch.setattr(manager, "is_installed", lambda: True)
        monkeypatch.setattr(manager, "write_config", lambda: None)
        monkeypatch.setattr(manager, "_health", lambda *a, **k: {"status": "ok"})
        monkeypatch.setattr(
            embedded_mod.subprocess,
            "Popen",
            lambda argv, **kw: SimpleNamespace(pid=PID, poll=lambda: None),
        )
        manager.start(port=50999, owner_pid=1000)

        assert manager.status().owner_pid == 1000
        manager.claim(2000)
        assert manager.status().owner_pid == 2000

    def test_an_unreadable_record_picks_a_new_port_loudly(
        self, manager, monkeypatch, caplog
    ):
        manager.root.mkdir(parents=True)
        manager.last_port_path.write_text("{ nope", encoding="utf-8")
        status, _ = self._start(manager, monkeypatch)
        assert status.port
        assert "last_port.json" in caplog.text

    def test_the_last_servers_time_wait_does_not_move_the_port(
        self, manager, monkeypatch
    ):
        first, _ = self._start(manager, monkeypatch)
        _leave_time_wait(first.port)
        second, _ = self._start(manager, monkeypatch)
        assert second.port == first.port

    @_posix_only
    def test_a_users_lemond_on_the_last_port_moves_gaias(self, manager, monkeypatch):
        """Its SO_REUSEPORT would let GAIA's lemond bind alongside it."""
        first, _ = self._start(manager, monkeypatch)
        with _listener(socket.AF_INET, "127.0.0.1", first.port, socket.SO_REUSEPORT):
            second, _ = self._start(manager, monkeypatch)
        assert second.port != first.port


@pytest.mark.allow_network
class TestPortProbe:
    """`_port_free` must say what lemond's own bind would say, never less safe."""

    def test_a_port_in_time_wait_is_free(self):
        port = _leave_time_wait()
        if sys.platform.startswith("linux"):
            # Proves the TIME_WAIT is real: the old SO_REUSEADDR probe fails here.
            with socket.socket() as old:
                old.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                with pytest.raises(OSError):
                    old.bind(("127.0.0.1", port))
        assert embedded_mod._port_free(port) is True

    @pytest.mark.parametrize(
        "option",
        [
            pytest.param(None, id="plain"),
            pytest.param(socket.SO_REUSEADDR, id="reuseaddr"),
            pytest.param(
                getattr(socket, "SO_REUSEPORT", None), id="reuseport", marks=_posix_only
            ),
            pytest.param(
                getattr(socket, "SO_EXCLUSIVEADDRUSE", None),
                id="exclusive",
                marks=_windows_only,
            ),
        ],
    )
    def test_a_live_loopback_listener_is_taken(self, option):
        with _listener(socket.AF_INET, "127.0.0.1", option=option) as port:
            assert embedded_mod._port_free(port) is False

    def test_a_live_wildcard_listener_is_taken(self):
        """Windows lets a 127.0.0.1 bind through a 0.0.0.0 listener."""
        with _listener(socket.AF_INET, "0.0.0.0") as port:
            assert embedded_mod._port_free(port) is False

    @_posix_only
    def test_a_same_user_reuseport_wildcard_listener_is_taken(self):
        with _listener(socket.AF_INET, "0.0.0.0", option=socket.SO_REUSEPORT) as port:
            assert embedded_mod._port_free(port) is False

    @_needs_ipv6
    def test_an_ipv6_only_listener_is_taken(self):
        """lemond binds ::1 as well as 127.0.0.1."""
        with _listener(socket.AF_INET6, "::1") as port:
            assert embedded_mod._port_free(port) is False

    @_needs_ipv6
    def test_a_dual_stack_listener_is_taken(self):
        sock = socket.socket(socket.AF_INET6, socket.SOCK_STREAM)
        try:
            sock.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 0)
            sock.bind(("::", 0))
            sock.listen(1)
            assert embedded_mod._port_free(sock.getsockname()[1]) is False
        finally:
            sock.close()

    def test_a_free_port_is_free_without_an_ipv6_stack(self, monkeypatch):
        port = embedded_mod._free_port()
        real_socket = socket.socket

        def _no_ipv6(family=socket.AF_INET, *args, **kwargs):
            if family == socket.AF_INET6:
                raise OSError(errno.EAFNOSUPPORT, "Address family not supported")
            return real_socket(family, *args, **kwargs)

        monkeypatch.setattr(embedded_mod.socket, "socket", _no_ipv6)
        assert embedded_mod._port_free(port) is True

    @_windows_only
    def test_windows_never_probes_with_reuse_options(self, monkeypatch):
        """SO_REUSEADDR there binds over a live listener."""
        set_options = []
        real_socket = socket.socket

        class _Spy(real_socket):
            def setsockopt(self, *args):
                set_options.append(args)
                return super().setsockopt(*args)

        monkeypatch.setattr(embedded_mod.socket, "socket", _Spy)
        assert embedded_mod._port_free(embedded_mod._free_port()) is True
        assert set_options == []


class TestStopWaitsForExit:
    def test_stop_does_not_return_while_the_process_lives(self, manager, monkeypatch):
        """The port closes before the process is gone; stop must wait for both."""
        _record(manager)
        own = _FakeProcess(
            [_own_exe(manager), "c", "c", "--port", str(PORT)],
            {"LEMONADE_API_KEY": KEY},
        )
        table = {PID: own}
        _processes(monkeypatch, table)
        killed = []
        monkeypatch.setattr(
            EmbeddedLemonade, "_health", lambda *a, **k: None if killed else {}
        )
        monkeypatch.setattr(EmbeddedLemonade, "_kill_tree", staticmethod(killed.append))
        polls = []

        def _sleep(seconds):
            polls.append(seconds)
            if len(polls) == 3:
                del table[PID]

        monkeypatch.setattr(embedded_mod.time, "sleep", _sleep)

        assert manager.stop() is True
        assert len(polls) >= 3
        assert PID not in table

    def test_a_process_that_never_exits_is_an_error_not_a_stop(
        self, manager, monkeypatch
    ):
        _record(manager)
        _processes(
            monkeypatch,
            {
                PID: _FakeProcess(
                    [_own_exe(manager), "c", "c", "--port", str(PORT)],
                    {"LEMONADE_API_KEY": KEY},
                )
            },
        )
        killed = []
        monkeypatch.setattr(
            EmbeddedLemonade, "_health", lambda *a, **k: None if killed else {}
        )
        monkeypatch.setattr(EmbeddedLemonade, "_kill_tree", staticmethod(killed.append))
        monkeypatch.setattr(embedded_mod.time, "sleep", lambda s: None)

        with pytest.raises(EmbeddedLemonadeError, match="still alive"):
            manager.stop(timeout=0.2)
        assert manager.state_path.exists(), "the record of a live server was dropped"


class TestOneStartAtATime:
    """`gaia init`, `gaia lemonade embedded start` and the daemon can all start
    the server; two at once must not leave one nobody can find."""

    def _fake_spawn(self, manager, monkeypatch, spawned, exits=False):
        monkeypatch.setattr(manager, "is_installed", lambda: True)
        monkeypatch.setattr(manager, "write_config", lambda: None)
        monkeypatch.setattr(
            EmbeddedLemonade,
            "_health",
            lambda self, port, key, timeout=3.0: None if exits else {"ok": 1},
        )

        def fake_popen(argv, **kwargs):
            time.sleep(0.05)
            pid = 5000 + len(spawned)
            spawned.append(pid)
            return SimpleNamespace(
                pid=pid,
                poll=lambda: 1 if exits else None,
                returncode=1 if exits else None,
                wait=lambda timeout=None: 1,
            )

        monkeypatch.setattr(embedded_mod.subprocess, "Popen", fake_popen)

    def test_concurrent_starts_spawn_one_server(self, tmp_path, monkeypatch):
        spawned = []
        managers = [EmbeddedLemonade(home=tmp_path) for _ in range(3)]
        for m in managers:
            self._fake_spawn(m, monkeypatch, spawned)
        threads = [
            threading.Thread(target=m.start, kwargs={"port": 50555}) for m in managers
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(10)

        assert len(spawned) == 1

    def test_a_failed_start_leaves_another_servers_record_alone(
        self, manager, monkeypatch
    ):
        """The loser of a port race must not delete the winner's state."""
        spawned = []
        self._fake_spawn(manager, monkeypatch, spawned, exits=True)
        monkeypatch.setattr(
            EmbeddedLemonade, "_kill_tree", staticmethod(lambda p: None)
        )
        real_write = manager._write_state

        def _winner_overwrites(state):
            real_write(state)
            if state["pid"] in spawned:  # the winner records itself meanwhile
                real_write({**state, "pid": 9999})

        monkeypatch.setattr(manager, "_write_state", _winner_overwrites)

        with pytest.raises(EmbeddedLemonadeError, match="exited immediately"):
            manager.start(port=50556)
        assert json.loads(manager.state_path.read_text())["pid"] == 9999


class TestUnload:
    def test_unload_posts_to_gaias_server_with_its_key(self, manager, monkeypatch):
        _record(manager)
        monkeypatch.setattr(EmbeddedLemonade, "_health", lambda *a, **k: {"ok": 1})
        sent = []

        class _Response:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def read(self):
                return b"{}"

        def _urlopen(request, timeout):
            sent.append(request)
            return _Response()

        monkeypatch.setattr(urllib.request, "urlopen", _urlopen)

        assert manager.unload_models() is True
        (request,) = sent
        assert request.full_url == f"http://127.0.0.1:{PORT}/api/v1/unload"
        assert request.get_method() == "POST"
        assert request.get_header("Authorization") == f"Bearer {KEY}"
        # No body, like LemonadeClient.unload_model(None): "unload everything".
        assert request.data is None

    def test_nothing_running_sends_nothing(self, manager, monkeypatch):
        monkeypatch.setattr(
            urllib.request,
            "urlopen",
            lambda *a, **k: pytest.fail("no server, no request"),
        )
        assert manager.unload_models() is False

    def test_a_refused_unload_fails_loudly(self, manager, monkeypatch):
        _record(manager)
        monkeypatch.setattr(EmbeddedLemonade, "_health", lambda *a, **k: {"ok": 1})

        def _refuse(request, timeout):
            raise urllib.error.URLError("connection reset")

        monkeypatch.setattr(urllib.request, "urlopen", _refuse)
        with pytest.raises(EmbeddedLemonadeError, match="did not unload"):
            manager.unload_models()
