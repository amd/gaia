# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Tests for the base Agent's Lemonade context-overflow health probe.

Issue #1139: the probe must carry ``Authorization: Bearer <key>`` when
``LEMONADE_API_KEY`` is set. Issue #2884: its loaded-context comparison must
use the active device profile, including when the agent resolves the device
from persisted configuration.

This probe runs in EVERY GAIA agent on context-overflow recovery — leaving
it unauthenticated would 401 against any remote authenticated Lemonade.
"""

import os
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture
def _minimal_agent():
    """Construct an Agent subclass with the abstract method stubbed.

    ``skip_lemonade=True`` avoids the LemonadeManager.ensure_ready call,
    so this fixture stays a pure unit test (no network, no subprocess).
    """
    from gaia.agents.base.agent import Agent

    class _StubAgent(Agent):
        def _register_tools(self):
            return None

    return _StubAgent(skip_lemonade=True, silent_mode=True)


@patch("httpx.get")
@patch("gaia.llm.lemonade_manager.LemonadeManager.get_base_url")
def test_is_loaded_ctx_too_small_sends_authorization_header_when_key_set(
    mock_get_base_url, mock_httpx_get, _minimal_agent
):
    mock_get_base_url.return_value = "http://localhost:13305/api/v1"
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"all_models_loaded": []}
    mock_httpx_get.return_value = mock_resp

    with patch.dict(os.environ, {"LEMONADE_API_KEY": "abc-1139"}, clear=False):
        _minimal_agent._is_loaded_ctx_too_small()

    headers = mock_httpx_get.call_args.kwargs.get("headers")
    assert headers == {"Authorization": "Bearer abc-1139"}


@patch("httpx.get")
@patch("gaia.llm.lemonade_manager.LemonadeManager.get_base_url")
def test_is_loaded_ctx_too_small_omits_authorization_header_when_no_key(
    mock_get_base_url, mock_httpx_get, _minimal_agent
):
    mock_get_base_url.return_value = "http://localhost:13305/api/v1"
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"all_models_loaded": []}
    mock_httpx_get.return_value = mock_resp

    env_no_key = {k: v for k, v in os.environ.items() if k != "LEMONADE_API_KEY"}
    with patch.dict(os.environ, env_no_key, clear=True):
        _minimal_agent._is_loaded_ctx_too_small()

    headers = mock_httpx_get.call_args.kwargs.get("headers")
    assert headers == {}, f"Expected empty headers dict, got {headers}"


@pytest.mark.parametrize(
    ("device", "loaded_ctx", "expected_too_small"),
    [
        ("npu", 32768, False),
        ("npu", 16384, True),
        ("gpu", 32768, True),
        (None, 32768, False),
    ],
)
@patch("httpx.get")
@patch("gaia.llm.lemonade_manager.LemonadeManager.get_base_url")
def test_is_loaded_ctx_too_small_uses_active_device_profile(
    mock_get_base_url,
    mock_httpx_get,
    device,
    loaded_ctx,
    expected_too_small,
    monkeypatch,
):
    """A correctly loaded NPU 32K model must not be routed to reload."""
    from gaia.agents.base.agent import Agent

    class _DeviceAgent(Agent):
        def _register_tools(self):
            return None

    agent = _DeviceAgent(skip_lemonade=True, silent_mode=True, device=device)
    if device is None:
        from types import SimpleNamespace

        monkeypatch.setattr(
            "gaia.config.GaiaConfig.load",
            lambda: SimpleNamespace(default_device="npu"),
        )
    mock_get_base_url.return_value = "http://localhost:13305/api/v1"
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "all_models_loaded": [
            {"type": "llm", "recipe_options": {"ctx_size": loaded_ctx}}
        ]
    }
    mock_httpx_get.return_value = mock_resp

    assert agent._is_loaded_ctx_too_small() is expected_too_small


@pytest.mark.parametrize("model", ["fireworks.gemma-4-31b-it", "amd.gemma-4-31b-it"])
def test_cloud_context_error_never_probes_local_model(
    monkeypatch, _minimal_agent, model
):
    probe = MagicMock()
    monkeypatch.setattr("httpx.get", probe)
    _minimal_agent.model_id = model

    assert _minimal_agent._is_loaded_ctx_too_small() is False
    probe.assert_not_called()


@pytest.mark.parametrize("model", ["fireworks.gemma-4-31b-it", "amd.gemma-4-31b-it"])
def test_cloud_startup_checks_server_without_loading_local_chat(monkeypatch, model):
    from gaia.agents.base.agent import Agent

    class CloudAgent(Agent):
        def _register_tools(self):
            return None

    health = MagicMock(return_value={"status": "ok"})
    local_init = MagicMock()
    monkeypatch.setattr("gaia.llm.lemonade_client.LemonadeClient.health_check", health)
    monkeypatch.setattr(
        "gaia.llm.lemonade_manager.LemonadeManager.ensure_ready", local_init
    )

    agent = CloudAgent(model_id=model, silent_mode=True)

    assert agent.model_id == model
    health.assert_called_once()
    local_init.assert_not_called()


def test_cloud_agent_without_url_uses_isolated_embedded_endpoint(monkeypatch, tmp_path):
    import json

    from gaia.agents.base.agent import Agent

    class CloudAgent(Agent):
        def _register_tools(self):
            return None

    monkeypatch.setenv("GAIA_HOME", str(tmp_path))
    monkeypatch.delenv("LEMONADE_BASE_URL", raising=False)
    monkeypatch.delenv("LEMONADE_API_KEY", raising=False)
    (tmp_path / "lemonade").mkdir()
    (tmp_path / "lemonade" / "state.json").write_text(
        json.dumps({"port": 63209, "api_key": "isolated-key"})
    )
    checked = []

    def health(client):
        checked.append((client.base_url, client.api_key))
        return {"status": "ok"}

    monkeypatch.setattr("gaia.llm.lemonade_client.LemonadeClient.health_check", health)
    agent = CloudAgent(model_id="fireworks.gemma-4-31b-it", silent_mode=True)

    assert checked == [("http://localhost:63209/api/v1", "isolated-key")]
    assert agent.chat.config.base_url == "http://localhost:63209/api/v1"
