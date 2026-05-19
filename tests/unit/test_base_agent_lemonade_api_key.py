# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Issue #1139: the base Agent's ``_is_loaded_ctx_too_small`` health probe
must carry ``Authorization: Bearer <key>`` when ``LEMONADE_API_KEY`` is set.

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
