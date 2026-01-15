# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""TDD tests for LLM client factory - write BEFORE implementation."""

from unittest.mock import patch

import pytest

# =============================================================================
# Import Tests (will fail until modules exist)
# =============================================================================


class TestImports:
    def test_can_import_create_client(self):
        from gaia.llm import create_client

        assert callable(create_client)

    def test_can_import_llm_client_abc(self):
        from abc import ABC

        from gaia.llm import LLMClient

        assert issubclass(LLMClient, ABC)

    def test_can_import_not_supported_error(self):
        from gaia.llm import NotSupportedError

        assert issubclass(NotSupportedError, Exception)


# =============================================================================
# Factory Tests
# =============================================================================


class TestCreateClientFactory:
    def test_create_client_returns_lemonade_provider(self):
        with patch("gaia.llm.providers.lemonade.LemonadeClient"):
            from gaia.llm import create_client

            client = create_client("lemonade")
            assert client.provider_name == "Lemonade"

    def test_create_client_invalid_provider_raises_valueerror(self):
        from gaia.llm import create_client

        with pytest.raises(ValueError, match="Unknown provider"):
            create_client("invalid_provider")

    def test_create_client_case_insensitive(self):
        with patch("gaia.llm.providers.lemonade.LemonadeClient"):
            from gaia.llm import create_client

            client = create_client("LEMONADE")
            assert client.provider_name == "Lemonade"

    def test_create_client_passes_kwargs(self):
        with patch("gaia.llm.providers.lemonade.LemonadeClient") as mock:
            from gaia.llm import create_client

            create_client("lemonade", base_url="http://custom:9000", model="test")
            mock.assert_called_with(base_url="http://custom:9000", model="test")


# =============================================================================
# NotSupportedError Tests
# =============================================================================


class TestNotSupportedError:
    def test_error_includes_provider_name(self):
        from gaia.llm import NotSupportedError

        error = NotSupportedError("Claude", "embed")
        assert "Claude" in str(error)

    def test_error_includes_method_name(self):
        from gaia.llm import NotSupportedError

        error = NotSupportedError("Claude", "embed")
        assert "embed" in str(error)


class TestClaudeNotSupported:
    def test_claude_embed_raises_not_supported(self):
        with patch("gaia.llm.providers.claude.anthropic"):
            from gaia.llm import NotSupportedError, create_client

            client = create_client("claude", api_key="test")

        with pytest.raises(NotSupportedError) as exc:
            client.embed(["text"])
        assert "Claude" in str(exc.value)
        assert "embed" in str(exc.value)

    def test_claude_load_model_raises_not_supported(self):
        with patch("gaia.llm.providers.claude.anthropic"):
            from gaia.llm import NotSupportedError, create_client

            client = create_client("claude", api_key="test")

        with pytest.raises(NotSupportedError):
            client.load_model("some-model")


class TestOpenAINotSupported:
    def test_openai_vision_raises_not_supported(self):
        with patch("openai.OpenAI"):
            from gaia.llm import NotSupportedError, create_client

            client = create_client("openai", api_key="test")

        with pytest.raises(NotSupportedError) as exc:
            client.vision([b"image"], "describe this")
        assert "OpenAI" in str(exc.value)


# =============================================================================
# Provider Name Tests
# =============================================================================


class TestProviderNames:
    def test_lemonade_provider_name(self):
        with patch("gaia.llm.providers.lemonade.LemonadeClient"):
            from gaia.llm import create_client

            client = create_client("lemonade")
            assert client.provider_name == "Lemonade"

    def test_openai_provider_name(self):
        with patch("openai.OpenAI"):
            from gaia.llm import create_client

            client = create_client("openai", api_key="test")
            assert client.provider_name == "OpenAI"

    def test_claude_provider_name(self):
        with patch("gaia.llm.providers.claude.anthropic"):
            from gaia.llm import create_client

            client = create_client("claude", api_key="test")
            assert client.provider_name == "Claude"


# =============================================================================
# ABC Interface Tests
# =============================================================================


class TestLLMClientABC:
    def test_cannot_instantiate_abc(self):
        from gaia.llm import LLMClient

        with pytest.raises(TypeError):
            LLMClient()

    def test_abc_has_generate_method(self):
        from gaia.llm import LLMClient

        assert hasattr(LLMClient, "generate")

    def test_abc_has_chat_method(self):
        from gaia.llm import LLMClient

        assert hasattr(LLMClient, "chat")

    def test_abc_has_embed_method(self):
        from gaia.llm import LLMClient

        assert hasattr(LLMClient, "embed")

    def test_abc_has_vision_method(self):
        from gaia.llm import LLMClient

        assert hasattr(LLMClient, "vision")
