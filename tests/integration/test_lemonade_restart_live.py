# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""GAIA's own Lemonade Server restarting under a session, on a real lemond (#5261).

Each test breaks a real, isolated lemond on purpose -- killed, hung mid-start,
never back, or taken down with the job of the session that started it -- and
checks what a session sees. Everything runs under a temp ``GAIA_HOME`` and user
profile with its own daemon and port (``util/lemonade_restart_harness.py``), so
no other Lemonade on the machine is touched. Cloud turns go to a local stub
registered as the ``amd`` provider: no account and no network.

Skips when no lemond can be had: none installed in ``~/.gaia`` to copy, and
GitHub unreachable or ``GAIA_SKIP_NETWORK_TESTS`` set. The same scenarios, plus
the stress run, are ``python util/lemonade_restart_harness.py repro|stress``.
"""

import importlib.util
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]

pytestmark = [pytest.mark.integration]


def _harness():
    spec = importlib.util.spec_from_file_location(
        "lemonade_restart_harness", REPO / "util" / "lemonade_restart_harness.py"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules.setdefault(spec.name, module)
    spec.loader.exec_module(module)
    return module


def _lemond_available() -> bool:
    from gaia.version import LEMONADE_VERSION

    if (Path.home() / ".gaia" / "lemonade" / "dist" / LEMONADE_VERSION).is_dir():
        return True
    if os.environ.get("GAIA_SKIP_NETWORK_TESTS"):
        return False
    try:
        urllib.request.urlopen(
            urllib.request.Request("https://github.com", method="HEAD"), timeout=10
        )
        return True
    except urllib.error.HTTPError:
        return True
    except (urllib.error.URLError, OSError):
        return False


@pytest.fixture(scope="module")
def h():
    return _harness()


@pytest.fixture(scope="module")
def gaia(h, tmp_path_factory):
    """One isolated home, daemon and lemond for the module; always torn down."""
    if not _lemond_available():
        pytest.skip("no lemond installed to copy, and it cannot be downloaded")
    saved = os.environ.get("GAIA_HOME")
    with h.isolated(tmp_path_factory.mktemp("restart")) as g:
        yield g
    if saved is None:
        os.environ.pop("GAIA_HOME", None)
    else:
        os.environ["GAIA_HOME"] = saved


@pytest.fixture
def healthy(gaia):
    """Each test starts from a running daemon and a healthy server."""
    gaia.start_daemon()
    assert gaia.wait_healthy(), "the isolated server did not come up"
    return gaia


windows_only = pytest.mark.skipif(sys.platform != "win32", reason="Windows job objects")


def test_a_first_request_during_a_restart_answers(h, healthy):
    turn = h.scenario_first_request_during_restart(healthy)
    assert turn.ok, turn.detail


def test_model_switch_during_a_restart_answers(h, healthy):
    """The reported failure: on main this ends "Lemonade Server is not reachable"."""
    turn = h.scenario_model_switch_during_restart(healthy)
    assert turn.ok, turn.detail
    assert h.NOT_REACHABLE not in turn.detail


def test_model_switch_after_the_server_died_answers(h, healthy):
    turn = h.scenario_killed_then_model_switch(healthy)
    assert turn.ok, turn.detail


def test_a_client_built_with_no_server_recorded_follows_the_new_one(h, healthy):
    turn = h.scenario_stale_port_client(healthy)
    assert turn.ok, turn.detail
    assert turn.port_after == healthy.state()["port"] != turn.port_before


def test_a_server_that_never_returns_fails_within_the_bound(h, healthy):
    turn = h.scenario_never_comes_back(healthy)
    assert not turn.ok
    assert turn.reconnecting, "the session was never told it was reconnecting"
    assert "gaia daemon status" in turn.detail
    assert "lemonade-server serve" not in turn.detail
    # The wait is 60 s; on Windows each refused connect to the dead port
    # costs ~2 s before the wait starts and between its polls.
    assert turn.elapsed <= h.RECONNECT_BOUND + 35, turn.elapsed


@windows_only
def test_closing_the_session_that_started_the_daemon_leaves_it_running(h, healthy):
    """The root cause: the session's job used to take the daemon and lemond."""
    turn = h.scenario_session_job_closed(healthy)
    assert turn.extra["daemon_survived"], "closing session 1 killed the daemon"
    assert turn.extra["lemond_survived"], "closing session 1 killed lemond"
    assert turn.ok, turn.detail


def test_the_users_own_server_is_never_waited_on_or_restarted(h, healthy):
    turn = h.scenario_users_own_server_is_never_waited_on(healthy)
    assert not turn.ok
    assert not turn.reconnecting
    assert turn.extra["daemon_asked"] == 0
    assert turn.extra["gaia_server_untouched"]
    assert not turn.extra["users_server_restarted"]


def test_a_remote_server_is_never_waited_on(h, healthy):
    turn = h.scenario_remote_server_is_never_waited_on(healthy)
    assert not turn.ok
    assert not turn.reconnecting
    assert turn.extra["daemon_asked"] == 0
    assert turn.extra["gaia_server_untouched"]


def test_an_idle_stop_then_a_switch_brings_it_back(h, healthy):
    turn = h.scenario_idle_stop_then_request(healthy)
    assert turn.extra["idle_stopped"], "the idle timeout never stopped the server"
    assert turn.ok, turn.detail
