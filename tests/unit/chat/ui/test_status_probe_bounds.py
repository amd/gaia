# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""/api/system/status answers in bounded time and says why Lemonade is unknown.

A connect timeout means nothing is answering, the same as a refused
connection, so the UI shows "not running" rather than "busy". A server that
accepts the connection but never finishes answering no longer holds the
endpoint for the sum of every probe's timeout.
"""

import asyncio
import time

import httpx
import pytest
from fastapi.testclient import TestClient

from gaia.ui.routers import system as system_mod
from gaia.ui.server import create_app


class _Client:
    def __init__(self, get):
        self._get = get

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc):
        return False

    async def get(self, url, **_kwargs):
        return await self._get(url)


@pytest.fixture
def status_with(monkeypatch):
    def _status(get):
        monkeypatch.setattr(httpx, "AsyncClient", lambda *a, **k: _Client(get))
        return TestClient(create_app(db_path=":memory:")).get("/api/system/status")

    return _status


@pytest.mark.allow_network
def test_a_connect_timeout_reads_as_not_running(status_with):
    async def get(url):
        raise httpx.ConnectTimeout("timed out connecting", request=None)

    body = status_with(get).json()

    assert body["lemonade_running"] is False
    assert body["lemonade_error"] is None
    assert body["lemonade_probe"] == "down"


@pytest.mark.allow_network
def test_a_server_that_never_answers_is_reported_in_bounded_time(
    status_with, monkeypatch
):
    monkeypatch.setattr(system_mod, "_STATUS_PROBE_BUDGET_S", 0.5, raising=False)

    async def get(url):
        await asyncio.sleep(30)

    started = time.monotonic()
    body = status_with(get).json()

    assert time.monotonic() - started < 5
    assert body["lemonade_probe"] == "timed_out"
    assert "did not answer" in body["lemonade_error"]


@pytest.mark.allow_network
def test_slow_extra_probes_do_not_make_a_running_server_look_down(
    status_with, monkeypatch
):
    monkeypatch.setattr(system_mod, "_STATUS_PROBE_BUDGET_S", 0.5)

    class _Ok:
        status_code = 200

        @staticmethod
        def json():
            return {"model_loaded": "Gemma-4-E4B-it-GGUF"}

    async def get(url):
        if url.endswith("/health"):
            return _Ok()
        await asyncio.sleep(30)

    body = status_with(get).json()

    assert body["lemonade_running"] is True
    assert body["lemonade_probe"] == "ok"
    assert body["lemonade_error"] is None
    assert any("skipped" in w for w in body["probe_warnings"])
