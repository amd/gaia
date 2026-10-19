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


# Spawn attempts made outside any test's own guard, e.g. by a thread a test
# left running: (the test running at the time, or "between tests").
_STRAY_SPAWN_ATTEMPTS: list = []


@pytest.fixture(scope="session", autouse=True)
def _forbid_daemon_spawn_all_session():
    """Refuse the real daemon spawn for the whole session, not just inside tests.

    The per-test guard below is undone at each test's teardown, so a thread a
    test leaves running (an app's start-up job, a worker) could reach the real
    spawn between tests and leave a daemon running after the session. This
    keeps it refused, and fails the session if anything tried.
    """
    from gaia.daemon import client

    def _refuse(*_args, **_kwargs):
        _STRAY_SPAWN_ATTEMPTS.append(
            os.environ.get("PYTEST_CURRENT_TEST", "between tests")
        )
        raise RuntimeError(_DAEMON_SPAWN_MESSAGE)

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(client, "_spawn_and_wait", _refuse)
        yield
    if _STRAY_SPAWN_ATTEMPTS:
        raise RuntimeError(
            f"{_DAEMON_SPAWN_MESSAGE} A thread left running by an earlier test "
            f"tried it {len(_STRAY_SPAWN_ATTEMPTS)} time(s), during: "
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
