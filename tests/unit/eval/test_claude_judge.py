# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Unit tests for ``gaia.eval.claude`` (the Claude judge client).

All Anthropic API calls are mocked — no real API key or network needed.
Tests cover: init validation, cost calculation, get_completion,
get_completion_with_usage, and count_tokens.
"""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

# ---------------------------------------------------------------------------
# Shared mocks — we need anthropic + bs4 importable before importing ClaudeClient
# ---------------------------------------------------------------------------


def _make_mock_anthropic():
    """Build a mock anthropic module with an Anthropic client constructor."""
    mock_module = MagicMock()
    mock_module.Anthropic = MagicMock()
    return mock_module


# ---------------------------------------------------------------------------
# Initialization
# ---------------------------------------------------------------------------


class TestClaudeClientInit:
    def test_raises_on_missing_api_key(self, monkeypatch):
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        monkeypatch.setattr("gaia.eval.claude.anthropic", _make_mock_anthropic())
        monkeypatch.setattr("gaia.eval.claude.BeautifulSoup", MagicMock())

        from gaia.eval.claude import ClaudeClient

        with pytest.raises(ValueError, match="ANTHROPIC_API_KEY"):
            ClaudeClient()

    def test_raises_on_missing_anthropic(self, monkeypatch):
        monkeypatch.setattr("gaia.eval.claude.anthropic", None)
        monkeypatch.setattr("gaia.eval.claude.BeautifulSoup", MagicMock())

        from gaia.eval.claude import ClaudeClient

        with pytest.raises(ImportError, match="anthropic"):
            ClaudeClient()

    def test_raises_on_missing_bs4(self, monkeypatch):
        monkeypatch.setattr("gaia.eval.claude.anthropic", _make_mock_anthropic())
        monkeypatch.setattr("gaia.eval.claude.BeautifulSoup", None)
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key-123")

        from gaia.eval.claude import ClaudeClient

        with pytest.raises(ImportError, match="bs4"):
            ClaudeClient()

    def test_success_with_valid_env(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key-123")
        mock_anthropic = _make_mock_anthropic()
        monkeypatch.setattr("gaia.eval.claude.anthropic", mock_anthropic)
        monkeypatch.setattr("gaia.eval.claude.BeautifulSoup", MagicMock())

        from gaia.eval.claude import ClaudeClient

        client = ClaudeClient(model="claude-sonnet-4-6")
        assert client.model == "claude-sonnet-4-6"
        assert client.api_key == "test-key-123"
        mock_anthropic.Anthropic.assert_called_once()


# ---------------------------------------------------------------------------
# Cost calculation
# ---------------------------------------------------------------------------


class TestCalculateCost:
    @pytest.fixture()
    def client(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
        monkeypatch.setattr("gaia.eval.claude.anthropic", _make_mock_anthropic())
        monkeypatch.setattr("gaia.eval.claude.BeautifulSoup", MagicMock())
        from gaia.eval.claude import ClaudeClient

        return ClaudeClient(model="claude-sonnet-4-6")

    def test_known_model_pricing(self, client):
        cost = client.calculate_cost(1_000_000, 1_000_000)
        # claude-sonnet-4-6: $3/MTok input, $15/MTok output
        assert cost["input_cost"] == pytest.approx(3.0, abs=1e-4)
        assert cost["output_cost"] == pytest.approx(15.0, abs=1e-4)
        assert cost["total_cost"] == pytest.approx(18.0, abs=1e-4)

    def test_small_token_count(self, client):
        cost = client.calculate_cost(100, 50)
        assert cost["input_cost"] == pytest.approx(100 / 1_000_000 * 3.0, abs=1e-6)
        assert cost["output_cost"] == pytest.approx(50 / 1_000_000 * 15.0, abs=1e-6)

    def test_unknown_model_uses_default(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
        monkeypatch.setattr("gaia.eval.claude.anthropic", _make_mock_anthropic())
        monkeypatch.setattr("gaia.eval.claude.BeautifulSoup", MagicMock())
        from gaia.eval.claude import ClaudeClient

        c = ClaudeClient(model="claude-future-9000")
        cost = c.calculate_cost(1_000_000, 1_000_000)
        # Default pricing matches Sonnet: $3/$15
        assert cost["total_cost"] == pytest.approx(18.0, abs=1e-4)


# ---------------------------------------------------------------------------
# get_completion
# ---------------------------------------------------------------------------


class TestGetCompletion:
    @pytest.fixture()
    def client(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
        mock_anthropic = _make_mock_anthropic()
        monkeypatch.setattr("gaia.eval.claude.anthropic", mock_anthropic)
        monkeypatch.setattr("gaia.eval.claude.BeautifulSoup", MagicMock())
        from gaia.eval.claude import ClaudeClient

        c = ClaudeClient(model="claude-sonnet-4-6")
        return c

    def test_returns_content(self, client):
        mock_content = [SimpleNamespace(text="Hello world")]
        client.client.messages.create.return_value = SimpleNamespace(
            content=mock_content
        )
        result = client.get_completion("test prompt")
        assert result == mock_content
        client.client.messages.create.assert_called_once()

    def test_propagates_api_error(self, client):
        client.client.messages.create.side_effect = RuntimeError("API down")
        with pytest.raises(RuntimeError, match="API down"):
            client.get_completion("test")


# ---------------------------------------------------------------------------
# get_completion_with_usage
# ---------------------------------------------------------------------------


class TestGetCompletionWithUsage:
    @pytest.fixture()
    def client(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
        monkeypatch.setattr("gaia.eval.claude.anthropic", _make_mock_anthropic())
        monkeypatch.setattr("gaia.eval.claude.BeautifulSoup", MagicMock())
        from gaia.eval.claude import ClaudeClient

        return ClaudeClient(model="claude-sonnet-4-6")

    def test_returns_usage_and_cost(self, client):
        mock_msg = SimpleNamespace(
            content=[SimpleNamespace(text="response")],
            usage=SimpleNamespace(input_tokens=500, output_tokens=200),
        )
        client.client.messages.create.return_value = mock_msg

        result = client.get_completion_with_usage("prompt")
        assert result["content"] == mock_msg.content
        assert result["usage"]["input_tokens"] == 500
        assert result["usage"]["output_tokens"] == 200
        assert result["usage"]["total_tokens"] == 700
        assert "cost" in result
        assert result["cost"]["total_cost"] > 0


# ---------------------------------------------------------------------------
# count_tokens
# ---------------------------------------------------------------------------


class TestCountTokens:
    @pytest.fixture()
    def client(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
        monkeypatch.setattr("gaia.eval.claude.anthropic", _make_mock_anthropic())
        monkeypatch.setattr("gaia.eval.claude.BeautifulSoup", MagicMock())
        from gaia.eval.claude import ClaudeClient

        return ClaudeClient(model="claude-sonnet-4-6")

    def test_delegates_to_sdk(self, client):
        client.client.messages.count_tokens.return_value = SimpleNamespace(
            input_tokens=42
        )
        result = client.count_tokens("test prompt")
        assert result.input_tokens == 42
        client.client.messages.count_tokens.assert_called_once_with(
            model="claude-sonnet-4-6",
            messages=[{"role": "user", "content": "test prompt"}],
        )
