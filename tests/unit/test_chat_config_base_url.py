# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Tests for ChatConfig.base_url defaulting to None (respects LEMONADE_BASE_URL env var)."""

from unittest.mock import MagicMock, patch

from gaia.chat.sdk import ChatConfig, ChatSDK


def test_chat_config_base_url_default_is_none():
    """ChatConfig default base_url is None (defers to env var / LLM factory)."""
    config = ChatConfig()
    assert config.base_url is None


def test_chat_config_base_url_custom():
    """ChatConfig accepts a custom base_url."""
    config = ChatConfig(base_url="http://remote:9000/api/v1")
    assert config.base_url == "http://remote:9000/api/v1"


def test_chat_sdk_passes_none_base_url_to_create_client():
    """ChatSDK forwards base_url=None to create_client when using default config."""
    with patch("gaia.chat.sdk.create_client") as mock_create:
        mock_create.return_value = MagicMock()
        ChatSDK(ChatConfig())
        call_kwargs = mock_create.call_args[1]
        assert "base_url" in call_kwargs
        assert call_kwargs["base_url"] is None


def test_chat_sdk_passes_custom_base_url_to_create_client():
    """ChatSDK forwards a custom base_url to create_client."""
    with patch("gaia.chat.sdk.create_client") as mock_create:
        mock_create.return_value = MagicMock()
        ChatSDK(ChatConfig(base_url="http://custom:8888/api/v1"))
        call_kwargs = mock_create.call_args[1]
        assert call_kwargs["base_url"] == "http://custom:8888/api/v1"
