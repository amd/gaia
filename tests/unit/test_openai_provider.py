# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Unit tests for OpenAIProvider — chat, generate, embed, stream, errors."""

from unittest.mock import MagicMock, patch

import pytest

from gaia.llm.exceptions import NotSupportedError

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def mock_openai_module():
    """Patch the openai module so OpenAIProvider never hits the network."""
    mock_mod = MagicMock()
    mock_client_instance = MagicMock()
    mock_mod.OpenAI.return_value = mock_client_instance
    with patch.dict("sys.modules", {"openai": mock_mod}):
        yield mock_mod, mock_client_instance


@pytest.fixture()
def provider(mock_openai_module):
    """Return an OpenAIProvider backed by the mocked openai module."""
    from gaia.llm.providers.openai_provider import OpenAIProvider

    return OpenAIProvider(api_key="sk-test", model="gpt-4o")


@pytest.fixture()
def client(mock_openai_module):
    """Shortcut to the mocked openai.OpenAI() instance."""
    return mock_openai_module[1]


# ---------------------------------------------------------------------------
# Initialization
# ---------------------------------------------------------------------------


class TestOpenAIProviderInit:
    """Constructor and basic properties."""

    def test_provider_name(self, provider):
        assert provider.provider_name == "OpenAI"

    def test_default_model(self, mock_openai_module):
        from gaia.llm.providers.openai_provider import OpenAIProvider

        p = OpenAIProvider(api_key="sk-test")
        assert p._model == "gpt-4o"

    def test_custom_model(self, mock_openai_module):
        from gaia.llm.providers.openai_provider import OpenAIProvider

        p = OpenAIProvider(api_key="sk-test", model="gpt-4-turbo")
        assert p._model == "gpt-4-turbo"

    def test_system_prompt_stored(self, mock_openai_module):
        from gaia.llm.providers.openai_provider import OpenAIProvider

        p = OpenAIProvider(api_key="sk-test", system_prompt="You are helpful.")
        assert p._system_prompt == "You are helpful."

    def test_extra_kwargs_ignored(self, mock_openai_module):
        from gaia.llm.providers.openai_provider import OpenAIProvider

        p = OpenAIProvider(api_key="sk-test", base_url="http://x", unknown_arg=True)
        assert p._model == "gpt-4o"


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
        assert call_kwargs.kwargs["model"] == "gpt-4o"

    def test_model_override(self, provider, client):
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = "ok"
        client.chat.completions.create.return_value = mock_response

        provider.chat([{"role": "user", "content": "Hi"}], model="gpt-4-turbo")
        call_kwargs = client.chat.completions.create.call_args
        assert call_kwargs.kwargs["model"] == "gpt-4-turbo"

    def test_system_prompt_prepended(self, mock_openai_module):
        from gaia.llm.providers.openai_provider import OpenAIProvider

        p = OpenAIProvider(api_key="sk-test", system_prompt="Be concise.")
        _, mock_client = mock_openai_module

        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = "ok"
        mock_client.chat.completions.create.return_value = mock_response

        p.chat([{"role": "user", "content": "Hi"}])
        call_kwargs = mock_client.chat.completions.create.call_args
        messages = call_kwargs.kwargs["messages"]
        assert messages[0]["role"] == "system"
        assert messages[0]["content"] == "Be concise."
        assert messages[1]["role"] == "user"

    def test_no_system_prompt_by_default(self, provider, client):
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = "ok"
        client.chat.completions.create.return_value = mock_response

        provider.chat([{"role": "user", "content": "Hi"}])
        call_kwargs = client.chat.completions.create.call_args
        messages = call_kwargs.kwargs["messages"]
        assert messages[0]["role"] == "user"

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

    def _make_empty_chunk(self):
        chunk = MagicMock()
        chunk.choices = [MagicMock()]
        chunk.choices[0].delta.content = None
        return chunk

    def _make_no_choices_chunk(self):
        chunk = MagicMock()
        chunk.choices = []
        return chunk

    def test_stream_yields_content(self, provider, client):
        chunks = [self._make_chunk("Hello"), self._make_chunk(" world")]
        client.chat.completions.create.return_value = iter(chunks)

        result = provider.chat([{"role": "user", "content": "Hi"}], stream=True)
        pieces = list(result)
        assert pieces == ["Hello", " world"]

    def test_stream_skips_empty_deltas(self, provider, client):
        chunks = [
            self._make_chunk("A"),
            self._make_empty_chunk(),
            self._make_chunk("B"),
        ]
        client.chat.completions.create.return_value = iter(chunks)

        pieces = list(provider.chat([{"role": "user", "content": "Hi"}], stream=True))
        assert pieces == ["A", "B"]

    def test_stream_skips_no_choices(self, provider, client):
        chunks = [
            self._make_chunk("X"),
            self._make_no_choices_chunk(),
            self._make_chunk("Y"),
        ]
        client.chat.completions.create.return_value = iter(chunks)

        pieces = list(provider.chat([{"role": "user", "content": "Hi"}], stream=True))
        assert pieces == ["X", "Y"]


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

    def test_generate_with_model_override(self, provider, client):
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = "ok"
        client.chat.completions.create.return_value = mock_response

        provider.generate("test", model="gpt-3.5-turbo")
        call_kwargs = client.chat.completions.create.call_args
        assert call_kwargs.kwargs["model"] == "gpt-3.5-turbo"


# ---------------------------------------------------------------------------
# embed()
# ---------------------------------------------------------------------------


class TestEmbed:
    """embed() returns a list of embedding vectors."""

    def test_embed_returns_vectors(self, provider, client):
        item1 = MagicMock()
        item1.embedding = [0.1, 0.2, 0.3]
        item2 = MagicMock()
        item2.embedding = [0.4, 0.5, 0.6]
        mock_response = MagicMock()
        mock_response.data = [item1, item2]
        client.embeddings.create.return_value = mock_response

        result = provider.embed(["hello", "world"])
        assert result == [[0.1, 0.2, 0.3], [0.4, 0.5, 0.6]]

    def test_embed_uses_default_model(self, provider, client):
        mock_response = MagicMock()
        mock_response.data = []
        client.embeddings.create.return_value = mock_response

        provider.embed(["text"])
        call_kwargs = client.embeddings.create.call_args
        assert call_kwargs.kwargs["model"] == "text-embedding-3-small"

    def test_embed_custom_model(self, provider, client):
        mock_response = MagicMock()
        mock_response.data = []
        client.embeddings.create.return_value = mock_response

        provider.embed(["text"], model="text-embedding-ada-002")
        call_kwargs = client.embeddings.create.call_args
        assert call_kwargs.kwargs["model"] == "text-embedding-ada-002"


# ---------------------------------------------------------------------------
# Unsupported methods (inherited from LLMClient)
# ---------------------------------------------------------------------------


class TestUnsupportedMethods:
    """Methods not implemented by OpenAIProvider raise NotSupportedError."""

    def test_vision_not_supported(self, provider):
        with pytest.raises(NotSupportedError, match="OpenAI.*vision"):
            provider.vision([b"img"], "describe")

    def test_get_performance_stats_not_supported(self, provider):
        with pytest.raises(NotSupportedError, match="OpenAI.*get_performance_stats"):
            provider.get_performance_stats()

    def test_load_model_not_supported(self, provider):
        with pytest.raises(NotSupportedError, match="OpenAI.*load_model"):
            provider.load_model("gpt-4o")

    def test_unload_model_not_supported(self, provider):
        with pytest.raises(NotSupportedError, match="OpenAI.*unload_model"):
            provider.unload_model()
