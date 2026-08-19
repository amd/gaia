# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Unit tests for OrcaRouterProvider — chat, generate, stream, env config."""

from unittest.mock import MagicMock, patch

import pytest

from gaia.llm.exceptions import NotSupportedError

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def mock_openai_module():
    """Patch the openai module so OrcaRouterProvider never hits the network."""
    mock_mod = MagicMock()
    mock_client_instance = MagicMock()
    mock_mod.OpenAI.return_value = mock_client_instance
    with patch.dict("sys.modules", {"openai": mock_mod}):
        yield mock_mod, mock_client_instance


@pytest.fixture()
def provider(mock_openai_module):
    """Return an OrcaRouterProvider backed by the mocked openai module."""
    from gaia.llm.providers.orcarouter import OrcaRouterProvider

    return OrcaRouterProvider(api_key="sk-orca-test", model="orcarouter/auto")


@pytest.fixture()
def client(mock_openai_module):
    """Shortcut to the mocked openai.OpenAI() instance."""
    return mock_openai_module[1]


# ---------------------------------------------------------------------------
# Initialization
# ---------------------------------------------------------------------------


class TestOrcaRouterProviderInit:
    """Constructor and basic properties."""

    def test_provider_name(self, provider):
        assert provider.provider_name == "OrcaRouter"

    def test_default_base_url(self, mock_openai_module):
        from gaia.llm.providers.orcarouter import (
            ORCAROUTER_DEFAULT_BASE_URL,
            OrcaRouterProvider,
        )

        mock_mod, mock_client = mock_openai_module
        OrcaRouterProvider(api_key="sk-orca-test")
        mock_mod.OpenAI.assert_called_once_with(
            api_key="sk-orca-test",
            base_url=ORCAROUTER_DEFAULT_BASE_URL,
        )

    def test_custom_base_url(self, mock_openai_module):
        from gaia.llm.providers.orcarouter import OrcaRouterProvider

        mock_mod, _ = mock_openai_module
        OrcaRouterProvider(
            api_key="sk-orca-test", base_url="https://custom.orca.example/v1"
        )
        mock_mod.OpenAI.assert_called_once_with(
            api_key="sk-orca-test",
            base_url="https://custom.orca.example/v1",
        )

    def test_default_model(self, mock_openai_module):
        from gaia.llm.providers.orcarouter import (
            ORCAROUTER_DEFAULT_MODEL,
            OrcaRouterProvider,
        )

        p = OrcaRouterProvider(api_key="sk-orca-test")
        assert p._model == ORCAROUTER_DEFAULT_MODEL

    def test_env_fallbacks(self, mock_openai_module, monkeypatch):
        from gaia.llm.providers.orcarouter import OrcaRouterProvider

        monkeypatch.setenv("ORCAROUTER_API_KEY", "sk-orca-env")
        monkeypatch.setenv("ORCAROUTER_BASE_URL", "https://env.orca.example/v1")
        monkeypatch.setenv("ORCAROUTER_MODEL", "deepseek/deepseek-v4-pro")

        mock_mod, mock_client = mock_openai_module
        p = OrcaRouterProvider()
        assert p._model == "deepseek/deepseek-v4-pro"
        mock_mod.OpenAI.assert_called_once_with(
            api_key="sk-orca-env",
            base_url="https://env.orca.example/v1",
        )


# ---------------------------------------------------------------------------
# chat() — non-streaming
# ---------------------------------------------------------------------------


class TestChat:
    """chat() delegates to OpenAI SDK and returns content."""

    def test_returns_message_content(self, provider, client):
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = "Hello!"
        client.chat.completions.create.return_value = mock_response

        result = provider.chat([{"role": "user", "content": "Hi"}], stream=False)
        assert result == "Hello!"

    def test_uses_default_model(self, provider, client):
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = "ok"
        client.chat.completions.create.return_value = mock_response

        provider.chat([{"role": "user", "content": "Hi"}])
        call_kwargs = client.chat.completions.create.call_args
        assert call_kwargs.kwargs["model"] == "orcarouter/auto"

    def test_model_override(self, provider, client):
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = "ok"
        client.chat.completions.create.return_value = mock_response

        provider.chat(
            [{"role": "user", "content": "Hi"}], model="deepseek/deepseek-v4-pro"
        )
        call_kwargs = client.chat.completions.create.call_args
        assert call_kwargs.kwargs["model"] == "deepseek/deepseek-v4-pro"

    def test_extra_kwargs_passed_through(self, provider, client):
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = "ok"
        client.chat.completions.create.return_value = mock_response

        provider.chat(
            [{"role": "user", "content": "Hi"}],
            temperature=0.5,
            max_tokens=100,
        )
        call_kwargs = client.chat.completions.create.call_args
        assert call_kwargs.kwargs["temperature"] == 0.5
        assert call_kwargs.kwargs["max_tokens"] == 100


# ---------------------------------------------------------------------------
# chat() — streaming
# ---------------------------------------------------------------------------


class TestChatStreaming:
    """chat(stream=True) returns an iterator of text chunks."""

    def _make_chunk(self, content):
        chunk = MagicMock()
        chunk.choices = [MagicMock()]
        chunk.choices[0].delta.content = content
        return chunk

    def test_stream_yields_content(self, provider, client):
        chunks = [self._make_chunk("Hello"), self._make_chunk(" world")]
        client.chat.completions.create.return_value = iter(chunks)

        result = provider.chat([{"role": "user", "content": "Hi"}], stream=True)
        pieces = list(result)
        assert pieces == ["Hello", " world"]


# ---------------------------------------------------------------------------
# generate()
# ---------------------------------------------------------------------------


class TestGenerate:
    """generate() wraps prompt into a user message and delegates to chat()."""

    def test_generate_non_streaming(self, provider, client):
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = "42"
        client.chat.completions.create.return_value = mock_response

        result = provider.generate("What is 6*7?")
        assert result == "42"

        call_kwargs = client.chat.completions.create.call_args
        messages = call_kwargs.kwargs["messages"]
        assert len(messages) == 1
        assert messages[0]["role"] == "user"
        assert messages[0]["content"] == "What is 6*7?"


# ---------------------------------------------------------------------------
# Unsupported methods
# ---------------------------------------------------------------------------


class TestUnsupportedMethods:
    """Methods not supported by OrcaRouterProvider raise NotSupportedError."""

    def test_embed_not_supported(self, provider):
        with pytest.raises(NotSupportedError, match="OrcaRouter.*embed"):
            provider.embed(["text"])

    def test_vision_not_supported(self, provider):
        with pytest.raises(NotSupportedError, match="OrcaRouter.*vision"):
            provider.vision([b"img"], "describe")

    def test_get_performance_stats_not_supported(self, provider):
        with pytest.raises(
            NotSupportedError, match="OrcaRouter.*get_performance_stats"
        ):
            provider.get_performance_stats()

    def test_load_model_not_supported(self, provider):
        with pytest.raises(NotSupportedError, match="OrcaRouter.*load_model"):
            provider.load_model("orcarouter/auto")


# ---------------------------------------------------------------------------
# Factory registration
# ---------------------------------------------------------------------------


class TestFactory:
    """create_client resolves the orcarouter provider name."""

    def test_create_client_orcarouter(self, mock_openai_module):
        from gaia.llm import create_client

        client = create_client("orcarouter", api_key="sk-orca-test")
        assert client.provider_name == "OrcaRouter"

    def test_create_client_orcarouter_case_insensitive(self, mock_openai_module):
        from gaia.llm import create_client

        client = create_client("ORCAROUTER", api_key="sk-orca-test")
        assert client.provider_name == "OrcaRouter"
