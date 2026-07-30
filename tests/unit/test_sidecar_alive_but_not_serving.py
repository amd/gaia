# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""A sidecar that is alive but no longer serving must be reported as such.

The startup health check (``_wait_for_health``) runs once and is never
re-checked, and ``is_running`` only asks ``poll()`` whether the PROCESS exists.
Between them they cannot see a sidecar that came up, passed its handshake, and
later stopped serving — a blocked event loop, a wedged credential-store read, a
hung dependency. The process stays alive through all of it, so the daemon goes
on reporting "running" and the only symptom is an unrelated caller timing out
much later against a row that had nothing to do with the fault.

Observed in the field: an email sidecar that answered ``/health`` and
``/version``, then stopped answering every route while its process stayed up.
What triggers that is environment-specific and not the point — the supervision
gap is that the daemon could not TELL, and these tests pin the re-check that
lets it: a live process whose port answers nothing is a loud, typed, actionable
failure at the moment a caller tries to use it.
"""

from __future__ import annotations

import subprocess
import sys
import threading

import pytest

from gaia.daemon.sidecars import manager as mgr
from gaia.daemon.sidecars.errors import (
    SidecarNotRunningError,
    SidecarUnresponsiveError,
)
from gaia.daemon.sidecars.spec import AgentSidecarSpec

_TOY_SPEC = AgentSidecarSpec(
    agent_id="toy",
    service_id="gaia-agent-toy",
    display_name="Toy Agent",
    expected_api_major="1",
    token_env_var="GAIA_TOY_SIDECAR_TOKEN",
    mode_env_var="GAIA_TOY_AGENT_MODE",
    cache_dir_name="toy",
)


@pytest.fixture(name="alive_process")
def _alive_process():
    """A real process that stays alive and listens on nothing."""
    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(300)"])
    try:
        yield proc
    finally:
        proc.kill()
        proc.wait(timeout=10)


def _manager_bound_to(proc, port: int) -> mgr.AgentSidecarManager:
    m = mgr.AgentSidecarManager(_TOY_SPEC)
    m._proc = proc
    m.port = port
    m.base_url = f"http://127.0.0.1:{port}"
    return m


def test_alive_process_that_serves_nothing_is_not_reported_healthy(
    alive_process, tmp_path
):
    """The shape the daemon could not see: process up, port dead, state 'running'."""
    port = mgr.find_free_port()
    m = _manager_bound_to(alive_process, port)
    m.log_dir = tmp_path

    # What the daemon used to rely on, and why it was fooled.
    assert m.is_running is True

    with pytest.raises(SidecarUnresponsiveError) as exc:
        m.check_responsive(timeout=1.0)

    message = str(exc.value)
    # The error has to be actionable on its own: what failed, what to run, and
    # where to look. A bare "unreachable" leaves the user where they started.
    assert "alive" in message
    assert str(alive_process.pid) in message
    assert "gaia daemon start-agent toy" in message
    assert str(tmp_path) in message


def test_dead_process_is_reported_as_not_running_not_unresponsive(tmp_path):
    """The two failure modes stay distinguishable — different causes, different fixes."""
    proc = subprocess.Popen([sys.executable, "-c", ""])
    proc.wait(timeout=10)

    m = _manager_bound_to(proc, mgr.find_free_port())
    m.log_dir = tmp_path

    assert m.is_running is False
    with pytest.raises(SidecarNotRunningError):
        m.check_responsive(timeout=1.0)


def test_responsive_sidecar_passes(alive_process, tmp_path, monkeypatch):
    """A sidecar answering /health is not disturbed by the re-check."""
    m = _manager_bound_to(alive_process, mgr.find_free_port())
    m.log_dir = tmp_path

    class _Ok:
        status_code = 200

    monkeypatch.setattr(m, "_http_get", lambda url, timeout: _Ok())
    m.check_responsive(timeout=1.0)  # must not raise


def test_non_200_health_is_unresponsive(alive_process, tmp_path, monkeypatch):
    """A 503 from /health is not a pass — it is a sidecar that cannot serve."""
    m = _manager_bound_to(alive_process, mgr.find_free_port())
    m.log_dir = tmp_path

    class _Down:
        status_code = 503

    monkeypatch.setattr(m, "_http_get", lambda url, timeout: _Down())
    with pytest.raises(SidecarUnresponsiveError, match="503"):
        m.check_responsive(timeout=1.0)


def test_relay_connection_refuses_a_wedged_sidecar(alive_process, tmp_path):
    """registry.connection() is the relay choke point — it must not hand out a
    base_url for a sidecar that cannot answer, or every relayed call inherits
    the wedge as an opaque timeout."""
    from gaia.daemon.sidecars.registry import SidecarRegistry

    registry = SidecarRegistry({"toy": _TOY_SPEC})
    m = _manager_bound_to(alive_process, mgr.find_free_port())
    m.log_dir = tmp_path
    registry._managers["toy"] = (m, threading.Lock())

    with pytest.raises(SidecarUnresponsiveError):
        registry.connection("toy")


def test_wedged_and_dead_details_keep_the_phrases_the_tui_matches_on(
    alive_process, tmp_path
):
    """These two messages are a cross-language contract, not just prose.

    The TUI tells a relay-authored refusal apart from a mailbox that refused
    something by matching these phrases (``relayGaveUp`` in
    ``tui/internal/ui/preflight/check.go``); without that it renders a wedged
    sidecar as a broken mailbox and offers a browser sign-in that fixes
    nothing. Rewording either message is a real behaviour change on the Go
    side, so it fails here — at the source — instead of only in a Go fixture.
    """
    from gaia.daemon.sidecars.registry import SidecarRegistry

    m = _manager_bound_to(alive_process, mgr.find_free_port())
    m.log_dir = tmp_path
    with pytest.raises(SidecarUnresponsiveError) as wedged:
        m.check_responsive(timeout=1.0)
    assert "is alive but did not answer" in str(wedged.value).lower()

    registry = SidecarRegistry({"toy": _TOY_SPEC})
    with pytest.raises(SidecarNotRunningError) as dead:
        registry.connection("toy")
    assert "no running sidecar" in str(dead.value).lower()
