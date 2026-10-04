# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""The Agent UI status endpoint reports its own probe failures.

Catalog, stats, device and disk probes used to swallow their errors, so the
status response looked complete while fields silently kept their defaults.
Each failure now lands in ``probe_warnings`` (and the log), and memory upkeep
no longer stops silently when an agent's memory methods can't be wired.
"""

import logging
import sqlite3
from unittest.mock import MagicMock

import httpx
import pytest
from fastapi.testclient import TestClient

from gaia.ui.routers import system as system_mod
from gaia.ui.server import create_app

_LOADED = {"model_loaded": "Gemma-4-E4B-it-GGUF"}


class _Resp:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code

    def json(self):
        return self._payload


class _FakeAsyncClient:
    """Routes GETs by path suffix; an Exception value is raised, unknown paths 404."""

    def __init__(self, routes):
        self._routes = routes

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc):
        return False

    async def get(self, url, params=None, **_kwargs):
        key = "/models?show_all" if params and params.get("show_all") else None
        for suffix, value in self._routes.items():
            if (key and suffix == key) or (not key and url.endswith(suffix)):
                if isinstance(value, Exception):
                    raise value
                if isinstance(value, _Resp):
                    return value
                return _Resp(value)
        return _Resp({}, status_code=404)


@pytest.fixture(autouse=True)
def _reset_probe_log_state():
    system_mod._last_probe_warning.clear()
    yield
    system_mod._last_probe_warning.clear()


@pytest.fixture
def status_with(monkeypatch):
    def _get(routes):
        monkeypatch.setattr(
            httpx, "AsyncClient", lambda *a, **k: _FakeAsyncClient(routes)
        )
        return TestClient(create_app(db_path=":memory:")).get("/api/system/status")

    return _get


@pytest.mark.allow_network
def test_healthy_probes_report_no_warnings(status_with):
    body = status_with(
        {
            "/health": _LOADED,
            "/models": {"data": []},
            "/stats": {"tokens_per_second": 42.0},
            "/system-info": {"devices": {}},
        }
    ).json()

    assert body["probe_warnings"] == []
    assert body["tokens_per_second"] == 42.0


@pytest.mark.allow_network
def test_stats_and_device_failures_are_reported(status_with, caplog):
    with caplog.at_level(logging.WARNING, logger="gaia.ui.routers.system"):
        body = status_with(
            {
                "/health": _LOADED,
                "/models": {"data": []},
                "/stats": httpx.ReadTimeout("timed out"),
                "/system-info": {"devices": "not-a-dict"},
            }
        ).json()

    warnings = body["probe_warnings"]
    assert any("inference stats" in w and "timed out" in w for w in warnings)
    assert any("device info" in w for w in warnings)
    assert body["tokens_per_second"] is None
    # Supplementary probe failures never flip the server to "down".
    assert body["lemonade_running"] is True
    assert body["lemonade_error"] is None
    assert any("inference stats" in r.getMessage() for r in caplog.records)


@pytest.mark.allow_network
def test_catalog_failure_leaves_download_state_unknown(status_with):
    body = status_with(
        {
            "/health": {},
            "/models?show_all": httpx.ConnectTimeout("catalog slow"),
            "/models": {"data": []},
            "/stats": {},
            "/system-info": {"devices": {}},
        }
    ).json()

    assert body["model_downloaded"] is None
    assert any(
        "is downloaded" in w and "catalog slow" in w for w in body["probe_warnings"]
    )


@pytest.mark.allow_network
def test_catalog_http_error_is_reported(status_with):
    body = status_with(
        {
            "/health": {},
            "/models?show_all": _Resp({}, status_code=500),
            "/models": {"data": []},
            "/stats": {},
            "/system-info": {"devices": {}},
        }
    ).json()

    assert body["model_downloaded"] is None
    assert any("HTTP 500" in w for w in body["probe_warnings"])


@pytest.mark.allow_network
def test_disk_failure_is_reported(status_with, monkeypatch):
    def _boom(_path):
        raise PermissionError("denied")

    monkeypatch.setattr("gaia.ui.server.shutil.disk_usage", _boom)
    body = status_with({"/health": _LOADED, "/models": {"data": []}}).json()

    assert any("free disk space" in w for w in body["probe_warnings"])


def test_repeated_probe_failure_logs_warning_once(caplog):
    from gaia.ui.models import SystemStatus

    with caplog.at_level(logging.DEBUG, logger="gaia.ui.routers.system"):
        for _ in range(3):
            status = SystemStatus()
            system_mod._probe_failed(status, "stats", "Could not read stats: x")
            assert status.probe_warnings == ["Could not read stats: x"]

    levels = [r.levelno for r in caplog.records if "read stats" in r.getMessage()]
    assert levels.count(logging.WARNING) == 1


# ── memory upkeep wiring ─────────────────────────────────────────────────


@pytest.fixture
def memory_router(monkeypatch):
    from gaia.ui.routers import memory as mem

    monkeypatch.setattr(mem, "_consolidate_fn", None)
    monkeypatch.setattr(mem, "_reconcile_fn", None)
    return mem


def _memory_agent_class():
    from gaia.agents.base.memory import MemoryMixin

    class _Agent(MemoryMixin):
        def __init__(self):  # pylint: disable=super-init-not-called
            pass

    return _Agent


def test_memory_agent_registers_upkeep(memory_router):
    from gaia.ui._chat_helpers import _register_agent_memory_ops

    agent = _memory_agent_class()()
    _register_agent_memory_ops(agent)

    assert memory_router._consolidate_fn == agent.consolidate_old_sessions
    assert memory_router._reconcile_fn == agent.reconcile_memory


def test_agent_without_memory_is_skipped(memory_router):
    from gaia.ui._chat_helpers import _register_agent_memory_ops

    _register_agent_memory_ops(object())

    assert memory_router._consolidate_fn is None
    assert memory_router._reconcile_fn is None


def test_renamed_memory_method_fails_loudly(memory_router, monkeypatch):
    from gaia.agents.base.memory import MemoryMixin
    from gaia.ui._chat_helpers import _register_agent_memory_ops

    monkeypatch.delattr(MemoryMixin, "reconcile_memory")

    with pytest.raises(AttributeError, match="reconcile_memory"):
        _register_agent_memory_ops(_memory_agent_class()())


def test_close_store_failure_is_logged(memory_router, monkeypatch, caplog):
    store = MagicMock()
    store.close.side_effect = sqlite3.OperationalError("database is locked")
    monkeypatch.setattr(memory_router, "_store", store)

    with caplog.at_level(logging.WARNING, logger="gaia.ui.routers.memory"):
        memory_router.close_store()

    assert memory_router._store is None
    assert any("database is locked" in r.getMessage() for r in caplog.records)
