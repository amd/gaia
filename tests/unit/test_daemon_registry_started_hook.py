# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Spec for the OAuth forward-out-on-spawn hook (issue #2304).

``SidecarRegistry._fire_started`` runs the daemon's ``on_started`` callback
AFTER a fresh ``start()`` succeeds (sidecar healthy) — this is where the OAuth
forward-out push (#2154) is wired. Before this file nothing verified that a
successful spawn actually fires the hook, that an ALREADY-running attach does
NOT re-fire it, or that a hook failure is swallowed (fail-open) rather than
failing a healthy spawn. A regression in either the wiring or the fail-open
guard would ship silently, leaving sidecars without forwarded credentials.

Also covers the server's ``_on_started`` closure (``server._build_registry``):
it must call ``forwarder.forward_all`` with the manager's base_url + bearer, and
return early when the manager has no base_url.
"""

from __future__ import annotations

import logging
import time as _t

import pytest

from gaia.daemon.sidecars.spec import builtin_specs


class _FakeManager:
    """Mimics AgentSidecarManager's public surface used by SidecarRegistry."""

    _next_pid = [7000]

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


def _registry(on_started, *, manager_cls=_FakeManager):
    from gaia.daemon.sidecars.registry import SidecarRegistry

    reg = SidecarRegistry(
        {"email": builtin_specs()["email"]}, on_started=on_started
    )
    reg._manager_factory = manager_cls  # type: ignore[attr-defined]
    return reg


def test_fresh_spawn_fires_on_started_once_with_manager():
    calls = []
    reg = _registry(lambda aid, mgr: calls.append((aid, mgr)))

    entry = reg.ensure("email")

    assert entry["state"] == "running"
    assert len(calls) == 1
    agent_id, manager = calls[0]
    assert agent_id == "email"
    # The hook receives the SAME manager the sidecar spawned, so the forwarder
    # can resolve its base_url + bearer.
    assert manager.base_url == entry["base_url"]
    assert manager.auth_token == entry["token"]


def test_attach_to_running_does_not_refire_on_started():
    calls = []
    reg = _registry(lambda aid, mgr: calls.append(aid))

    reg.ensure("email")  # fresh spawn -> fires once
    reg.ensure("email")  # attach to the already-running sidecar -> must NOT fire

    assert calls == ["email"]


def test_on_started_failure_is_swallowed_and_spawn_still_succeeds(caplog):
    def _boom(agent_id, manager):
        raise RuntimeError("forward-out blew up")

    reg = _registry(_boom)

    with caplog.at_level(logging.WARNING):
        entry = reg.ensure("email")

    # A raising hook must NOT fail an otherwise-healthy spawn.
    assert entry["state"] == "running"
    assert entry["base_url"]
    # ...but it is NOT silent: the failure is logged loudly with context.
    assert any(
        "post-start hook" in rec.getMessage() and "email" in rec.getMessage()
        for rec in caplog.records
    )


def test_on_started_none_is_a_noop():
    reg = _registry(None)
    entry = reg.ensure("email")  # must not raise
    assert entry["state"] == "running"


# --- server._build_registry _on_started closure ---------------------------


class _RecordingForwarder:
    def __init__(self):
        self.calls = []

    def forward_all(self, agent_id, *, base_url, bearer):
        self.calls.append({"agent_id": agent_id, "base_url": base_url, "bearer": bearer})
        return {"agent_id": agent_id, "forwarded": [], "skipped": [], "errors": []}


def _build_registry_with(forwarder):
    from gaia.daemon.server import _build_registry

    reg = _build_registry({"email": builtin_specs()["email"]}, forwarder)
    reg._manager_factory = _FakeManager  # type: ignore[attr-defined]
    return reg


def test_server_on_started_closure_calls_forward_all_with_connection():
    forwarder = _RecordingForwarder()
    reg = _build_registry_with(forwarder)

    entry = reg.ensure("email")

    assert len(forwarder.calls) == 1
    call = forwarder.calls[0]
    assert call["agent_id"] == "email"
    assert call["base_url"] == entry["base_url"]
    assert call["bearer"] == entry["token"]


def test_server_on_started_closure_returns_early_without_base_url():
    class _NoUrlManager(_FakeManager):
        def start(self):
            super().start()
            self.base_url = None  # healthy but no base_url -> nothing to forward to

    forwarder = _RecordingForwarder()
    from gaia.daemon.server import _build_registry

    reg = _build_registry({"email": builtin_specs()["email"]}, forwarder)
    reg._manager_factory = _NoUrlManager  # type: ignore[attr-defined]

    reg.ensure("email")

    assert forwarder.calls == []  # no forward attempted without a base_url
