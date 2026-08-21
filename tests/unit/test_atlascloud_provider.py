# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Unit tests for the Atlas Cloud LLM provider."""

from unittest.mock import MagicMock, patch

import pytest

from gaia.llm.exceptions import NotSupportedError
from gaia.llm.providers.atlascloud import (
    DEFAULT_ATLASCLOUD_BASE_URL,
    DEFAULT_ATLASCLOUD_MODEL,
    AtlasCloudProvider,
)


@pytest.fixture()
def mock_openai_module():
    mock_mod = MagicMock()
    mock_client = MagicMock()
    mock_mod.OpenAI.return_value = mock_client
    with patch.dict("sys.modules", {"openai": mock_mod}):
        yield mock_mod, mock_client


class TestAtlasCloudProviderInit:
    def test_uses_explicit_configuration(self, mock_openai_module):
        mock_mod, _ = mock_openai_module

        provider = AtlasCloudProvider(
            api_key="atlas-test",
            model="openai/gpt-5",
            base_url="https://atlas.example/v1/",
            timeout=30,
        )

        mock_mod.OpenAI.assert_called_once_with(
            api_key="atlas-test",
            base_url="https://atlas.example/v1",
            timeout=30,
        )
        assert provider._model == "openai/gpt-5"
        assert provider.provider_name == "Atlas Cloud"

    def test_uses_environment_api_key(self, mock_openai_module, monkeypatch):
        mock_mod, _ = mock_openai_module
        monkeypatch.setenv("ATLASCLOUD_API_KEY", "atlas-env")

        AtlasCloudProvider()

        mock_mod.OpenAI.assert_called_once_with(
            api_key="atlas-env",
            base_url=DEFAULT_ATLASCLOUD_BASE_URL,
        )

    def test_explicit_api_key_overrides_environment(
        self, mock_openai_module, monkeypatch
    ):
        mock_mod, _ = mock_openai_module
        monkeypatch.setenv("ATLASCLOUD_API_KEY", "atlas-env")

        AtlasCloudProvider(api_key="atlas-explicit")

        assert mock_mod.OpenAI.call_args.kwargs["api_key"] == "atlas-explicit"

    def test_missing_api_key_fails_loudly(self, mock_openai_module, monkeypatch):
        monkeypatch.delenv("ATLASCLOUD_API_KEY", raising=False)

        with pytest.raises(ValueError, match="ATLASCLOUD_API_KEY"):
            AtlasCloudProvider()

    def test_default_model(self, mock_openai_module):
        provider = AtlasCloudProvider(api_key="atlas-test")

        assert provider._model == DEFAULT_ATLASCLOUD_MODEL


class TestAtlasCloudFactory:
    def test_create_client(self, mock_openai_module):
        from gaia.llm import create_client

        provider = create_client("atlascloud", api_key="atlas-test")

        assert isinstance(provider, AtlasCloudProvider)

    def test_create_client_is_case_insensitive(self, mock_openai_module):
        from gaia.llm import create_client

        provider = create_client("ATLASCLOUD", api_key="atlas-test")

        assert provider.provider_name == "Atlas Cloud"


class TestAtlasCloudChat:
    def test_chat_uses_default_model(self, mock_openai_module):
        _, client = mock_openai_module
        response = MagicMock()
        response.choices = [MagicMock()]
        response.choices[0].message.content = "hello"
        client.chat.completions.create.return_value = response
        provider = AtlasCloudProvider(api_key="atlas-test")

        result = provider.chat([{"role": "user", "content": "Hi"}])

        assert result == "hello"
        assert (
            client.chat.completions.create.call_args.kwargs["model"]
            == DEFAULT_ATLASCLOUD_MODEL
        )

    def test_embedding_fails_as_unsupported(self, mock_openai_module):
        provider = AtlasCloudProvider(api_key="atlas-test")

        with pytest.raises(NotSupportedError, match="Atlas Cloud.*embed"):
            provider.embed(["hello"])
