# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Every Lemonade lookup finds GAIA's own server, not a hard-coded default port."""

import json
import os

import pytest


@pytest.fixture
def own_server(monkeypatch, tmp_path):
    """GAIA's own Lemonade recorded on port 51234; nothing configured."""
    (tmp_path / "lemonade").mkdir()
    (tmp_path / "lemonade" / "state.json").write_text(
        json.dumps({"pid": os.getpid(), "port": 51234, "api_key": "own-key"}),
        encoding="utf-8",
    )
    monkeypatch.setenv("GAIA_HOME", str(tmp_path))
    for name in ("LEMONADE_BASE_URL", "LEMONADE_API_KEY", "GAIA_LEMONADE_EMBEDDED"):
        monkeypatch.delenv(name, raising=False)
    return "http://localhost:51234/api/v1"


def test_agent_ui_status_and_onboarding_follow_gaias_server(own_server):
    from gaia.ui.routers import onboarding, system

    assert system._get_lemonade_base_url() == own_server
    assert onboarding._get_lemonade_base_url() == own_server


def test_a_configured_server_still_wins(own_server, monkeypatch):
    from gaia.ui.routers import system

    monkeypatch.setenv("LEMONADE_BASE_URL", "http://gpu-box:13305")
    assert system._get_lemonade_base_url() == "http://gpu-box:13305/api/v1"


async def test_api_server_health_asks_gaias_server_with_its_key(
    own_server, monkeypatch
):
    from gaia.api import openai_server

    seen = {}

    class _Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {"status": "ok", "model_loaded": "Gemma-4-E4B-it-GGUF"}

    class _Client:
        def __init__(self, **_):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return False

        async def get(self, url, headers=None):
            seen["url"], seen["headers"] = url, headers
            return _Response()

    monkeypatch.setattr(openai_server.httpx, "AsyncClient", _Client)
    component = await openai_server._lemonade_health()

    assert seen["url"] == f"{own_server}/health"
    assert seen["headers"] == {"Authorization": "Bearer own-key"}
    assert component["url"] == own_server
