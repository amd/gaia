# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Spec for the daemon's OAuth re-forward timer (issues #2388 / #2159).

The daemon forwards SHORT-LIVED connector access tokens to a sidecar only at
spawn time (``on_started``). Without a periodic re-forward the forwarded token
expires (~1h; observed dying at ~2.6h uptime) and the sidecar fails every
subsequent Gmail call with a 401 and never self-recovers.

``ForwardRefresher`` is the missing timer: a daemon-owned background thread that
periodically re-mints and re-forwards each RUNNING sidecar's granted tokens so a
fresh token always arrives before the old one expires. This exercises the tick
logic and the loop's fail-loud-but-resilient guard without a real thread wall
clock or a live sidecar.
"""

from __future__ import annotations

import logging
import threading

import pytest

from gaia.daemon.forward_refresh import (
    DEFAULT_REFRESH_INTERVAL,
    ForwardRefresher,
    resolve_interval,
)


class _FakeRegistry:
    def __init__(self, connections):
        # connections: list[(agent_id, base_url, bearer)]
        self._connections = list(connections)

    def running_connections(self):
        return list(self._connections)


class _RecordingForwarder:
    """Records forward_all calls; optionally raises for a chosen agent."""

    def __init__(self, *, raise_for=None, summary_errors_for=None):
        self.calls = []
        self._raise_for = raise_for or set()
        self._summary_errors_for = summary_errors_for or set()

    def forward_all(self, agent_id, *, base_url, bearer):
        self.calls.append(agent_id)
        if agent_id in self._raise_for:
            raise RuntimeError(f"mint blew up for {agent_id}")
        errors = (
            [{"provider": "google", "error": "revoked"}]
            if agent_id in self._summary_errors_for
            else []
        )
        return {
            "agent_id": agent_id,
            "forwarded": [],
            "skipped": [],
            "errors": errors,
        }


def _refresher(registry, forwarder, **kw):
    return ForwardRefresher(registry, forwarder, **kw)


# --- tick -------------------------------------------------------------------


def test_tick_reforwards_every_running_sidecar():
    reg = _FakeRegistry(
        [("email", "http://127.0.0.1:9", "b1"), ("toy", "http://127.0.0.1:10", "b2")]
    )
    fwd = _RecordingForwarder()
    _refresher(reg, fwd).tick()
    assert sorted(fwd.calls) == ["email", "toy"]


def test_tick_no_running_sidecars_is_a_noop():
    fwd = _RecordingForwarder()
    _refresher(_FakeRegistry([]), fwd).tick()
    assert fwd.calls == []


def test_tick_one_sidecar_failure_does_not_stop_the_others(caplog):
    reg = _FakeRegistry(
        [("email", "http://127.0.0.1:9", "b1"), ("toy", "http://127.0.0.1:10", "b2")]
    )
    fwd = _RecordingForwarder(raise_for={"email"})
    with caplog.at_level(logging.WARNING):
        _refresher(reg, fwd).tick()
    # Both attempted even though 'email' raised.
    assert sorted(fwd.calls) == ["email", "toy"]
    # The failing agent is logged loudly, not swallowed silently.
    assert any("email" in rec.getMessage() for rec in caplog.records)


def test_tick_logs_warning_when_summary_reports_provider_errors(caplog):
    reg = _FakeRegistry([("email", "http://127.0.0.1:9", "b1")])
    fwd = _RecordingForwarder(summary_errors_for={"email"})
    with caplog.at_level(logging.WARNING):
        _refresher(reg, fwd).tick()
    assert any(
        "email" in rec.getMessage() and rec.levelno >= logging.WARNING
        for rec in caplog.records
    )


# --- interval resolution ----------------------------------------------------


def test_default_interval_is_well_below_one_hour():
    # The re-forward must land before a ~1h access token expires.
    assert 0 < DEFAULT_REFRESH_INTERVAL <= 900


def test_resolve_interval_reads_env_override(monkeypatch):
    monkeypatch.setenv("GAIA_DAEMON_FORWARD_REFRESH_INTERVAL", "42")
    assert resolve_interval() == 42.0


def test_resolve_interval_rejects_nonpositive(monkeypatch):
    monkeypatch.setenv("GAIA_DAEMON_FORWARD_REFRESH_INTERVAL", "0")
    # A non-positive interval would mean "never re-forward" — fail loud, don't
    # silently fall back.
    with pytest.raises(ValueError):
        resolve_interval()


def test_resolve_interval_default_when_unset(monkeypatch):
    monkeypatch.delenv("GAIA_DAEMON_FORWARD_REFRESH_INTERVAL", raising=False)
    assert resolve_interval() == DEFAULT_REFRESH_INTERVAL


# --- loop lifecycle ---------------------------------------------------------


def test_safe_tick_swallows_and_logs_unexpected_error(caplog):
    """A background maintenance loop must survive a transient failure to retry
    next tick — but loudly, with context (not a silent fallback)."""

    class _BoomRegistry:
        def running_connections(self):
            raise RuntimeError("registry exploded")

    refresher = _refresher(_BoomRegistry(), _RecordingForwarder())
    with caplog.at_level(logging.ERROR):
        refresher._safe_tick()  # must NOT raise
    assert any("re-forward" in rec.getMessage().lower() for rec in caplog.records)


def test_start_runs_at_least_one_tick_then_stops_cleanly():
    reg = _FakeRegistry([("email", "http://127.0.0.1:9", "b1")])
    fwd = _RecordingForwarder()
    ticked = threading.Event()

    class _Refresher(ForwardRefresher):
        def tick(self):
            super().tick()
            ticked.set()

    # Tiny interval so the very first wait() elapses quickly.
    refresher = _Refresher(reg, fwd, interval=0.01)
    refresher.start()
    try:
        assert ticked.wait(timeout=2.0), "refresher never ticked"
    finally:
        refresher.stop()
    assert "email" in fwd.calls
    assert not refresher.is_alive()


def test_stop_is_idempotent_and_safe_without_start():
    refresher = _refresher(_FakeRegistry([]), _RecordingForwarder())
    refresher.stop()  # never started — must not raise
    refresher.stop()
