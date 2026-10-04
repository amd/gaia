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


def test_agent_ui_status_links_gaias_server(own_server):
    from gaia.ui.models import SystemStatus

    assert SystemStatus().lemonade_url == "http://localhost:51234"


def test_a_configured_server_still_wins(own_server, monkeypatch):
    from gaia.ui.models import SystemStatus

    monkeypatch.setenv("LEMONADE_BASE_URL", "http://gpu-box:13305")
    assert SystemStatus().lemonade_url == "http://gpu-box:13305"


class _Resp:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload


def test_registry_model_lookup_asks_gaias_server_with_its_key(own_server, mocker):
    from gaia.agents.registry import get_lemonade_models

    get = mocker.patch(
        "requests.get", return_value=_Resp(200, {"data": [{"id": "m1"}]})
    )

    assert get_lemonade_models() == ["m1"]
    get.assert_called_once()
    assert get.call_args.args[0] == f"{own_server}/models"
    assert get.call_args.kwargs["headers"] == {"Authorization": "Bearer own-key"}


def test_chat_preflight_asks_gaias_server_with_its_key(own_server, mocker):
    from gaia.llm.lemonade_manager import LemonadeManager
    from gaia.ui import _chat_helpers

    # Nothing initialised the manager: the helper must not guess a port.
    mocker.patch.object(LemonadeManager, "_base_url", None)
    mocker.patch.object(_chat_helpers, "_eval_provider_kwargs", return_value={})
    model = "Gemma-4-E4B-it-GGUF"
    resident = {
        "all_models_loaded": [
            {
                "model_name": model,
                "type": "llm",
                "recipe": "llamacpp",
                "recipe_options": {"ctx_size": 10**7},
            }
        ]
    }
    get = mocker.patch("httpx.get", return_value=_Resp(200, resident))

    _chat_helpers._maybe_load_expected_model(model)

    get.assert_called_once()
    assert get.call_args.args[0] == f"{own_server}/health"
    assert get.call_args.kwargs["headers"] == {"Authorization": "Bearer own-key"}


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
