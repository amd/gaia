# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Spec for the sidecar-spawn test hook (issue #2539).

The daemon's normal resilience is to spawn-or-attach a stopped agent the next
time anything calls ``ensure`` — good behavior in production, but it means a
test that stops an agent (``gaia daemon stop-agent``) to verify the preflight
gate's "agent not running" messaging can never observe that state: the very
next probe silently respawns it. ``GAIA_TEST_INHIBIT_SIDECAR`` lets a test
hold the stopped state in place instead.
"""

from __future__ import annotations

import time as _t

import pytest

from gaia.daemon.sidecars.errors import SidecarInhibitedError, SidecarSpawnError
from gaia.daemon.sidecars.registry import INHIBIT_SIDECAR_ENV_VAR
from gaia.daemon.sidecars.spec import builtin_specs


class _FakeManager:
    """Mimics AgentSidecarManager's public surface used by SidecarRegistry."""

    _next_pid = [8000]

    def __init__(self, spec, mode=None, **kwargs):
        self.spec = spec
        self._mode_override = mode
        self._running = False
        self.port = None
        self.base_url = None
        self.api_version = "1.0"
        self.agent_version = "0.1.0"
        self.resolved_mode = None
        self.auth_token = f"tok-{spec.agent_id}"
        self.pid = None
        self.started_at = None
        self.start_calls = 0

    @property
    def mode(self):
        import os

        return self._mode_override or os.environ.get(self.spec.mode_env_var) or "user"

    @property
    def is_running(self):
        return self._running

    def start(self):
        self.start_calls += 1
        self.resolved_mode = self.mode
        _FakeManager._next_pid[0] += 1
        self.pid = _FakeManager._next_pid[0]
        self.port = 50000 + self.pid
        self.base_url = f"http://127.0.0.1:{self.port}"
        self.started_at = _t.time()
        self._running = True
        return self.base_url

    def shutdown(self):
        self._running = False


def _registry():
    from gaia.daemon.sidecars.registry import SidecarRegistry

    reg = SidecarRegistry({"email": builtin_specs()["email"]})
    reg._manager_factory = _FakeManager  # type: ignore[attr-defined]
    return reg


def test_inhibited_agent_id_refuses_to_spawn(monkeypatch):
    monkeypatch.setenv(INHIBIT_SIDECAR_ENV_VAR, "email")
    reg = _registry()

    with pytest.raises(SidecarInhibitedError) as exc:
        reg.ensure("email")

    assert INHIBIT_SIDECAR_ENV_VAR in str(exc.value)
    assert "email" in str(exc.value)
    # It's a SidecarSpawnError subclass, so the existing route mapping (502)
    # and any other SidecarSpawnError-handling caller needs no new branch.
    assert isinstance(exc.value, SidecarSpawnError)


def test_wildcard_inhibits_every_agent(monkeypatch):
    monkeypatch.setenv(INHIBIT_SIDECAR_ENV_VAR, "*")
    reg = _registry()

    with pytest.raises(SidecarInhibitedError):
        reg.ensure("email")


def test_other_agent_ids_are_unaffected(monkeypatch):
    monkeypatch.setenv(INHIBIT_SIDECAR_ENV_VAR, "gaia")
    reg = _registry()

    entry = reg.ensure("email")  # not in the inhibited list

    assert entry["state"] == "running"


def test_unset_env_var_never_inhibits(monkeypatch):
    monkeypatch.delenv(INHIBIT_SIDECAR_ENV_VAR, raising=False)
    reg = _registry()

    entry = reg.ensure("email")

    assert entry["state"] == "running"


def test_attaching_to_an_already_running_agent_ignores_inhibit(monkeypatch):
    # The hook holds a STOPPED agent stopped — it must never kill or block
    # access to one that is already up (that would be new production
    # behavior, not a test hold).
    reg = _registry()
    reg.ensure("email")  # fresh spawn, before the var is set

    monkeypatch.setenv(INHIBIT_SIDECAR_ENV_VAR, "email")
    entry = reg.ensure("email")  # attach path

    assert entry["state"] == "running"
