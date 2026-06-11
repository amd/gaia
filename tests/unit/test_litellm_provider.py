# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Tests for LiteLLM provider."""

import sys
import types
from unittest.mock import MagicMock, patch

import pytest


def _stub_litellm():
    """Install a stub litellm module so tests run without the real package."""
    fake = types.ModuleType("litellm")
    fake.completion = MagicMock(name="litellm.completion")
    fake.embedding = MagicMock(name="litellm.embedding")
    fake.drop_params = False
    sys.modules["litellm"] = fake
    return fake


class TestLiteLLMProviderName:
    def test_provider_name(self):
        fake = _stub_litellm()
        from gaia.llm.providers.litellm import LiteLLMProvider

        provider = LiteLLMProvider(api_key="test-key", model="gpt-4o")
        assert provider.provider_name == "LiteLLM"
        del sys.modules["litellm"]


class TestLiteLLMFactory:
    def test_create_client_litellm(self):
        _stub_litellm()
        from gaia.llm import create_client

        client = create_client("litellm", api_key="test-key")
        assert client.provider_name == "LiteLLM"
        del sys.modules["litellm"]

    def test_create_client_litellm_case_insensitive(self):
        _stub_litellm()
        from gaia.llm import create_client

        client = create_client("LITELLM", api_key="test-key")
        assert client.provider_name == "LiteLLM"
        del sys.modules["litellm"]


class TestLiteLLMChat:
    def test_chat_calls_litellm_completion(self):
        fake = _stub_litellm()
        fake.completion.return_value = MagicMock(
            choices=[MagicMock(message=MagicMock(content="Hello!"))]
        )
        from gaia.llm.providers.litellm import LiteLLMProvider

        provider = LiteLLMProvider(api_key="sk-test", model="gpt-4o")
        result = provider.chat([{"role": "user", "content": "Hi"}])

        assert result == "Hello!"
        fake.completion.assert_called_once()
        call_kwargs = fake.completion.call_args
        assert call_kwargs.kwargs["model"] == "gpt-4o"
        assert call_kwargs.kwargs["drop_params"] is True
        assert call_kwargs.kwargs["api_key"] == "sk-test"
        del sys.modules["litellm"]

    def test_chat_prepends_system_prompt(self):
        fake = _stub_litellm()
        fake.completion.return_value = MagicMock(
            choices=[MagicMock(message=MagicMock(content="OK"))]
        )
        from gaia.llm.providers.litellm import LiteLLMProvider

        provider = LiteLLMProvider(
            api_key="sk-test", model="gpt-4o", system_prompt="You are helpful."
        )
        provider.chat([{"role": "user", "content": "Hi"}])

        messages = fake.completion.call_args.kwargs["messages"]
        assert messages[0]["role"] == "system"
        assert messages[0]["content"] == "You are helpful."
        del sys.modules["litellm"]

    def test_chat_omits_api_key_when_not_set(self):
        fake = _stub_litellm()
        fake.completion.return_value = MagicMock(
            choices=[MagicMock(message=MagicMock(content="OK"))]
        )
        from gaia.llm.providers.litellm import LiteLLMProvider

        provider = LiteLLMProvider(model="gpt-4o")
        provider.chat([{"role": "user", "content": "Hi"}])

        assert "api_key" not in fake.completion.call_args.kwargs
        del sys.modules["litellm"]

    def test_chat_uses_override_model(self):
        fake = _stub_litellm()
        fake.completion.return_value = MagicMock(
            choices=[MagicMock(message=MagicMock(content="OK"))]
        )
        from gaia.llm.providers.litellm import LiteLLMProvider

        provider = LiteLLMProvider(model="gpt-4o")
        provider.chat(
            [{"role": "user", "content": "Hi"}],
            model="anthropic/claude-sonnet-4-6",
        )

        assert (
            fake.completion.call_args.kwargs["model"] == "anthropic/claude-sonnet-4-6"
        )
        del sys.modules["litellm"]


class TestLiteLLMGenerate:
    def test_generate_delegates_to_chat(self):
        fake = _stub_litellm()
        fake.completion.return_value = MagicMock(
            choices=[MagicMock(message=MagicMock(content="4"))]
        )
        from gaia.llm.providers.litellm import LiteLLMProvider

        provider = LiteLLMProvider(model="gpt-4o")
        result = provider.generate("What is 2+2?")

        assert result == "4"
        messages = fake.completion.call_args.kwargs["messages"]
        assert messages[0] == {"role": "user", "content": "What is 2+2?"}
        del sys.modules["litellm"]


class TestLiteLLMNotSupported:
    def test_vision_raises_not_supported(self):
        _stub_litellm()
        from gaia.llm import NotSupportedError
        from gaia.llm.providers.litellm import LiteLLMProvider

        provider = LiteLLMProvider(model="gpt-4o")
        with pytest.raises(NotSupportedError) as exc:
            provider.vision([b"image"], "describe this")
        assert "LiteLLM" in str(exc.value)
        del sys.modules["litellm"]

    def test_load_model_raises_not_supported(self):
        _stub_litellm()
        from gaia.llm import NotSupportedError
        from gaia.llm.providers.litellm import LiteLLMProvider

        provider = LiteLLMProvider(model="gpt-4o")
        with pytest.raises(NotSupportedError):
            provider.load_model("some-model")
        del sys.modules["litellm"]
