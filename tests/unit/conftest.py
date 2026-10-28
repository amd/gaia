# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""conftest for unit tests.

The CI ``test_unit.yml`` workflow sets ``GAIA_MEMORY_DISABLED=1`` so that
agents that import ``MemoryMixin`` can be instantiated without a running
Lemonade embedding server.  However the memory unit tests (test_memory_*.py)
mock out the embedder and need ``init_memory()`` to run its real flow.

This conftest auto-clears ``GAIA_MEMORY_DISABLED`` when collecting tests
from any ``test_memory_*.py`` file so the per-test mocks take effect.
"""

import os
import socket
import sys
import tempfile
from pathlib import Path

import pytest


@pytest.fixture
def mock_home(tmp_path, monkeypatch):
    """Redirect the process home directory at a per-test ``tmp_path``.

    Patches ``HOME``, ``USERPROFILE``, ``Path.home()``, and
    ``os.path.expanduser`` so code resolving the home dir through env vars,
    pathlib, or ``os.path`` lands in the sandbox — a real ``~/.gaia`` is never
    read or written. Returns the fake home so tests can build expected paths
    under it.
    """
    real_expanduser = os.path.expanduser

    def fake_expanduser(path):
        if path == "~":
            return str(tmp_path)
        if path.startswith(("~/", "~\\")):
            return str(tmp_path) + path[1:]
        return real_expanduser(path)

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
    monkeypatch.setattr(os.path, "expanduser", fake_expanduser)
    return tmp_path


@pytest.fixture(autouse=True)
def _restore_environ():
    """Undo any ``os.environ`` change a test leaves behind, so none depends on order.

    The code under test can write the environment too (a PATH refresh, a
    popped flag); without this the next test inherits it.
    """
    saved = dict(os.environ)
    yield
    if dict(os.environ) != saved:
        os.environ.clear()
        os.environ.update(saved)


@pytest.fixture(autouse=True)
def _isolate_gaia_config(tmp_path, monkeypatch):
    """Keep unit tests off the developer's real ``~/.gaia/config.json``.

    ``GAIA_CONFIG_FILE`` is resolved at import time, so patching ``HOME`` is
    not enough.
    """
    from gaia import config as config_mod

    monkeypatch.setattr(config_mod, "GAIA_CONFIG_DIR", tmp_path)
    monkeypatch.setattr(config_mod, "GAIA_CONFIG_FILE", tmp_path / "config.json")


#: The home this run started with; a test that moves it keeps ``<its home>/.gaia``.
_RUN_HOME = Path.home()


def _patch_default_gaia_home(mp, default_gaia_home) -> None:
    from gaia import config as config_mod
    from gaia import home as home_mod
    from gaia.connectors import _refresh_lock

    # gaia_home() lives in gaia.home; config re-exports it.
    mp.setattr(home_mod, "default_gaia_home", default_gaia_home)
    mp.setattr(config_mod, "default_gaia_home", default_gaia_home)
    # Bound at import; the connector lock is the one writer among those names.
    mp.setattr(_refresh_lock, "default_gaia_home", default_gaia_home)


# Session scope: module- and class-scoped fixtures build agents (and their
# databases) before any function-scoped fixture runs.
@pytest.fixture(scope="session", autouse=True)
def _session_gaia_home(tmp_path_factory):
    """Keep ``gaia_home()`` -- edit backups, the audit log, databases -- off the
    developer's real ``~/.gaia``.

    Only the ``~/.gaia`` fallback moves. GAIA_HOME and GAIA_CONFIG_DIR still win,
    and a test that patches ``Path.home`` or sets HOME/USERPROFILE still gets
    ``<that home>/.gaia``: setting GAIA_HOME here would override both.
    """
    isolated = tmp_path_factory.mktemp("home") / ".gaia"

    def default_gaia_home() -> Path:
        home = Path.home()
        return isolated if home == _RUN_HOME else home / ".gaia"

    with pytest.MonkeyPatch.context() as mp:
        _patch_default_gaia_home(mp, default_gaia_home)
        yield default_gaia_home


@pytest.fixture(autouse=True)
def _isolate_gaia_home(_session_gaia_home, monkeypatch):
    """Re-applied per test: a test that reloads ``gaia.config`` drops the patch."""
    _patch_default_gaia_home(monkeypatch, _session_gaia_home)


_DAEMON_SPAWN_MESSAGE = (
    "Unit test tried to spawn a real GAIA daemon (gaia.daemon.client."
    "_spawn_and_wait). Mock the daemon boundary instead — e.g. monkeypatch "
    "gaia.ui.email_sidecar.daemon_client.acquire_handle or "
    "gaia.daemon.client.start_or_attach. Real daemons belong in tests/integration/."
)


@pytest.fixture(scope="session", autouse=True)
def _isolate_daemon_home(tmp_path_factory):
    """Point the daemon's ``instance.json`` / ledger dir at a per-session tmp dir.

    Against the real ``~/.gaia/host``, ``_block_network`` makes the developer's
    live daemon fail its probe, so ``start_or_attach`` tree-kills it as hung.
    """
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("GAIA_DAEMON_HOME", str(tmp_path_factory.mktemp("daemon-home")))
        yield


# Spawn attempts made outside any test's own guard: one line per attempt
# naming the test running at the time, the thread, and the test code calling.
_STRAY_SPAWN_ATTEMPTS: list = []
_TESTS_DIR = os.path.join(
    os.path.normcase(os.path.dirname(os.path.dirname(__file__))), ""
)


def _describe_stray_spawn() -> str:
    """Where a spawn the per-test guard did not cover came from."""
    import threading
    import traceback

    test = os.environ.get("PYTEST_CURRENT_TEST", "between tests")
    thread = threading.current_thread().name
    caller = "no test code on its stack"
    for frame in reversed(traceback.extract_stack()):
        path = os.path.normcase(frame.filename)
        if path.startswith(_TESTS_DIR) and os.path.basename(path) != "conftest.py":
            caller = f"called from {frame.filename}:{frame.lineno} in {frame.name}"
            break
    return f"{test} on thread {thread}, {caller}"


@pytest.fixture(scope="session", autouse=True)
def _forbid_daemon_spawn_all_session():
    """Refuse the real daemon spawn for the whole session, not just inside tests.

    The per-test guard below is only active while a test runs, so a class- or
    module-scoped fixture (set up before it) or a thread a test leaves running
    could reach the real spawn and leave a daemon behind. This keeps it
    refused, and fails the session naming where each attempt came from.
    """
    from gaia.daemon import client

    def _refuse(*_args, **_kwargs):
        _STRAY_SPAWN_ATTEMPTS.append(_describe_stray_spawn())
        raise RuntimeError(_DAEMON_SPAWN_MESSAGE)

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(client, "_spawn_and_wait", _refuse)
        yield
    if _STRAY_SPAWN_ATTEMPTS:
        raise RuntimeError(
            f"{_DAEMON_SPAWN_MESSAGE} It was tried {len(_STRAY_SPAWN_ATTEMPTS)} "
            "time(s) outside the per-test guard. On MainThread during a test's "
            "setup, a class-, module- or session-scoped fixture did it; on any "
            "other thread, a thread an earlier test left running did. Attempts: "
            + "; ".join(sorted(set(_STRAY_SPAWN_ATTEMPTS)))
        )


@pytest.fixture(autouse=True)
def _forbid_daemon_spawn(monkeypatch):
    """Fail any unit test that reaches the real ``python -m gaia.daemon`` spawn.

    Callers wrap spawn errors (the email router turns them into a 503), so the
    attempt is also recorded and failed at teardown where nothing can swallow it.
    """
    from gaia.daemon import client

    attempts = []

    def _refuse(*_args, **_kwargs):
        attempts.append(True)
        raise RuntimeError(_DAEMON_SPAWN_MESSAGE)

    monkeypatch.setattr(client, "_spawn_and_wait", _refuse)
    yield
    if attempts:
        pytest.fail(_DAEMON_SPAWN_MESSAGE, pytrace=False)


@pytest.fixture(autouse=True)
def _no_automatic_lemonade_lease(monkeypatch):
    """Keep every LemonadeClient a test builds from leasing Lemonade.

    The lease renews on a background thread for the life of the process, so it
    would call whatever daemon-client mock a later test installs.
    """
    from gaia.llm import lemonade_service

    monkeypatch.setattr(lemonade_service, "_AUTO_LEASED", True)


def pytest_configure(config):
    config.addinivalue_line(
        "markers", "allow_network: opt out of the _block_network socket guard"
    )
    config.addinivalue_line(
        "markers",
        "real_lemonade: keep the caller's LEMONADE_BASE_URL; with "
        "GAIA_TEST_ALLOW_REAL_LEMONADE=1 also let the test spawn or kill a "
        "real Lemonade server",
    )


@pytest.fixture(autouse=True)
def _block_network(request, monkeypatch):
    """Prevent unit tests from making real network connections.

    Opt out with @pytest.mark.allow_network.
    """
    if request.node.get_closest_marker("allow_network"):
        yield
        return

    def _blocked_connect(*args, **kwargs):
        raise ConnectionError(
            "Unit tests must not make real network connections "
            "(mark the test @pytest.mark.allow_network if it uses a local socket)"
        )

    def _blocked_connect_ex(*args, **kwargs):
        return 1

    # Windows has no native socketpair(); asyncio builds its event-loop self
    # pipe over a loopback TCP connect, which the guard would otherwise block.
    real_connect = socket.socket.connect
    real_socketpair = socket.socketpair

    def _unguarded_socketpair(*args, **kwargs):
        socket.socket.connect = real_connect
        try:
            return real_socketpair(*args, **kwargs)
        finally:
            socket.socket.connect = _blocked_connect

    monkeypatch.setattr(socket, "socketpair", _unguarded_socketpair)
    monkeypatch.setattr(socket.socket, "connect", _blocked_connect)
    monkeypatch.setattr(socket.socket, "connect_ex", _blocked_connect_ex)
    yield


@pytest.fixture(autouse=True)
def _enable_memory_for_memory_tests(request, monkeypatch):
    """Auto-fixture: clear GAIA_MEMORY_DISABLED for memory test modules.

    Only applies to tests in modules whose filename starts with ``test_memory_``.
    Other tests continue to honour the env var (so non-memory agents init
    cleanly without Lemonade).
    """
    module_path = getattr(request.module, "__file__", "") or ""
    if "test_memory_" in os.path.basename(module_path):
        monkeypatch.delenv("GAIA_MEMORY_DISABLED", raising=False)


@pytest.fixture(autouse=True)
def _fresh_user_turns(monkeypatch):
    """Give each test its own process-wide user-turn count and idle clock.

    A test that fails between starting and finishing a turn would otherwise
    hold every later test's memory extraction.
    """
    memory = sys.modules.get("gaia.agents.base.memory")
    if memory is not None:
        monkeypatch.setattr(memory, "_USER_TURNS", memory._UserTurns())


# ── Real Lemonade guard ────────────────────────────────────────────────────
#
# Unit tests build clients aimed at the default port with faked server
# processes. Anything that then reaches a real spawn or kill lands on the
# developer's own Lemonade, so both are refused for the whole session --
# finalizers run after a test's mocks are gone, outside any per-test guard.

_LEMONADE_SERVER_BINARIES = frozenset(
    {
        "lemonadeserver",
        "lemonade-server",
        "lemonade-server-dev",
        "lemond",
        "lemonade-router",
    }
)
_KILL_COMMANDS = frozenset({"taskkill", "kill", "pkill", "killall"})
_UNROUTABLE_LEMONADE_URL = "http://127.0.0.1:1"
_REAL_LEMONADE_OPT_IN_ENV = "GAIA_TEST_ALLOW_REAL_LEMONADE"
_REAL_LEMONADE_MARKERS = ("real_lemonade", "integration", "real_model")
_REAL_LEMONADE_MESSAGE = (
    "Unit test tried to {action}. That is the Lemonade Server on this "
    "machine, not one the test owns. Mock the launch or kill boundary "
    "(subprocess.Popen, gaia.ports.terminate_pid), or mark the test "
    "@pytest.mark.real_lemonade and run it deliberately with "
    f"{_REAL_LEMONADE_OPT_IN_ENV}=1."
)

# Attempts made under the guard: (test running at the time, action).
_REAL_LEMONADE_ATTEMPTS: list = []
_REAL_LEMONADE_ALLOWED = {"current_test": False}


def _program(token) -> str:
    name = os.path.basename(str(token)).lower()
    return name[:-4] if name.endswith(".exe") else name


def _is_test_fixture(path) -> bool:
    """True for a file under the temp dir: a fake binary a test made itself."""
    try:
        resolved = os.path.normcase(os.path.realpath(str(path)))
    except (OSError, ValueError):
        return False
    tmp = os.path.normcase(os.path.realpath(tempfile.gettempdir()))
    return resolved.startswith(tmp + os.sep)


def _lemonade_process_name(pid) -> "str | None":
    """The name of *pid* when it is a real Lemonade server, else None.

    A Lemonade-named fake a test copied into its tmp dir is not real.
    """
    import psutil

    try:
        proc = psutil.Process(int(pid))
        name = proc.name()
        if _program(name) not in _LEMONADE_SERVER_BINARIES:
            return None
        exe = proc.exe()
    except psutil.AccessDenied:
        return name
    except (psutil.Error, ValueError, TypeError, OverflowError):
        return None
    return None if _is_test_fixture(exe) else name


def _real_lemonade_allowed() -> bool:
    """Only a marked test, run with the opt-in env var, and never between tests."""
    return _REAL_LEMONADE_ALLOWED["current_test"]


def _refuse_real_lemonade(action: str) -> None:
    _REAL_LEMONADE_ATTEMPTS.append(
        (os.environ.get("PYTEST_CURRENT_TEST", "between tests"), action)
    )
    raise RuntimeError(_REAL_LEMONADE_MESSAGE.format(action=action))


def _check_command(args) -> None:
    tokens = args.split() if isinstance(args, (str, bytes)) else list(args)
    tokens = [t.decode() if isinstance(t, bytes) else str(t) for t in tokens]
    if not tokens:
        return
    if _program(tokens[0]) in _KILL_COMMANDS:
        for token in tokens[1:]:
            if _program(token) in _LEMONADE_SERVER_BINARIES:
                _refuse_real_lemonade(f"kill Lemonade by name ({' '.join(tokens)})")
            if token.isdigit() and _lemonade_process_name(token):
                _refuse_real_lemonade(
                    f"kill PID {token} ({_lemonade_process_name(token)})"
                )
        return
    for token in tokens:
        if _program(token) in _LEMONADE_SERVER_BINARIES and not (
            os.path.isabs(token) and _is_test_fixture(token)
        ):
            _refuse_real_lemonade(f"start a real Lemonade server ({' '.join(tokens)})")


def _check_kill(pid) -> None:
    name = _lemonade_process_name(pid)
    if name:
        _refuse_real_lemonade(f"kill PID {pid} ({name})")


@pytest.fixture(scope="session", autouse=True)
def _forbid_real_lemonade_all_session():
    """Refuse spawning or killing a real Lemonade for the whole unit session."""
    import subprocess

    import psutil

    real_execute_child = subprocess.Popen._execute_child
    real_kill = os.kill
    real_killpg = getattr(os, "killpg", None)
    real_psutil = {
        name: getattr(psutil.Process, name)
        for name in ("terminate", "kill", "send_signal")
    }

    def execute_child(self, args, *rest, **kwargs):
        if not _real_lemonade_allowed():
            _check_command(args)
        return real_execute_child(self, args, *rest, **kwargs)

    def kill(pid, sig):
        if not _real_lemonade_allowed():
            _check_kill(pid)
        return real_kill(pid, sig)

    def killpg(pgid, sig):
        if not _real_lemonade_allowed():
            _check_kill(pgid)
        return real_killpg(pgid, sig)

    def guard_psutil(name):
        real = real_psutil[name]

        def guarded(self, *args, **kwargs):
            if not _real_lemonade_allowed():
                _check_kill(self.pid)
            return real(self, *args, **kwargs)

        return guarded

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(subprocess.Popen, "_execute_child", execute_child)
        mp.setattr(os, "kill", kill)
        if real_killpg is not None:
            mp.setattr(os, "killpg", killpg)
        for name in real_psutil:
            mp.setattr(psutil.Process, name, guard_psutil(name))
        yield
    outside = [a for a in _REAL_LEMONADE_ATTEMPTS if a[0] == "between tests"]
    if outside:
        raise RuntimeError(
            _REAL_LEMONADE_MESSAGE.format(action="reach a real Lemonade")
            + " Code running between tests (a finalizer or a leftover thread) "
            "tried: " + "; ".join(sorted({action for _, action in outside}))
        )


_LEMONADE_ENV_VARS = ("LEMONADE_BASE_URL", "LEMONADE_HOST", "LEMONADE_PORT")
# The caller's values, for the tests that opt in to keeping them.
_CALLER_LEMONADE_ENV: dict = {}


@pytest.fixture(scope="session", autouse=True)
def _unroutable_lemonade_all_session():
    """Aim the whole session at an unroutable Lemonade, without automatic leases.

    Class- and module-scoped fixtures run before any per-test guard; an agent
    built there would otherwise follow GAIA's real Lemonade and ask the daemon
    to start it.
    """
    from gaia.llm import lemonade_service

    _CALLER_LEMONADE_ENV.update({k: os.environ.get(k) for k in _LEMONADE_ENV_VARS})
    with pytest.MonkeyPatch.context() as mp:
        if os.environ.get(_REAL_LEMONADE_OPT_IN_ENV) != "1":
            mp.setenv("LEMONADE_BASE_URL", _UNROUTABLE_LEMONADE_URL)
            mp.delenv("LEMONADE_HOST", raising=False)
            mp.delenv("LEMONADE_PORT", raising=False)
        mp.setattr(lemonade_service, "_AUTO_LEASED", True)
        yield


@pytest.fixture(autouse=True)
def _forbid_real_lemonade(request, monkeypatch):
    """Point unit tests at an unroutable Lemonade, and fail one that reaches a real one.

    A @pytest.mark.real_lemonade (or integration / real_model) test keeps the
    caller's LEMONADE_BASE_URL; it may also spawn or kill a real Lemonade only
    when GAIA_TEST_ALLOW_REAL_LEMONADE=1 is set as well.
    """
    opted_in = any(request.node.get_closest_marker(m) for m in _REAL_LEMONADE_MARKERS)
    if opted_in:
        for name, value in _CALLER_LEMONADE_ENV.items():
            # Undo only what the session fixture planted; a value set since is deliberate.
            planted = _UNROUTABLE_LEMONADE_URL if name == "LEMONADE_BASE_URL" else None
            if os.environ.get(name) != planted:
                continue
            if value is None:
                monkeypatch.delenv(name, raising=False)
            else:
                monkeypatch.setenv(name, value)
    elif os.environ.get(_REAL_LEMONADE_OPT_IN_ENV) != "1":
        monkeypatch.setenv("LEMONADE_BASE_URL", _UNROUTABLE_LEMONADE_URL)
        monkeypatch.delenv("LEMONADE_HOST", raising=False)
        monkeypatch.delenv("LEMONADE_PORT", raising=False)
    _REAL_LEMONADE_ALLOWED["current_test"] = (
        opted_in and os.environ.get(_REAL_LEMONADE_OPT_IN_ENV) == "1"
    )
    before = len(_REAL_LEMONADE_ATTEMPTS)
    yield
    _REAL_LEMONADE_ALLOWED["current_test"] = False
    caught = _REAL_LEMONADE_ATTEMPTS[before:]
    if caught:
        pytest.fail(_REAL_LEMONADE_MESSAGE.format(action=caught[0][1]), pytrace=False)


@pytest.fixture
def gaia_owned_lemonade(monkeypatch):
    """Unset the guard's LEMONADE_BASE_URL for a test of GAIA running its own server.

    A set LEMONADE_BASE_URL means "the user's server", which GAIA never starts.
    The spawn/kill guard and the network block stay on.
    """
    monkeypatch.delenv("LEMONADE_BASE_URL", raising=False)
