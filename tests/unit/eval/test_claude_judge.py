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


def _build_client(monkeypatch, temperature=None, model="claude-sonnet-4-6"):
    """Construct a ClaudeClient with mocked anthropic/bs4 deps.

    ``temperature`` is only forwarded to the constructor when not ``None`` so
    tests can exercise the true default-argument path (no kwarg at all), not
    just an explicit ``temperature=None``.
    """
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setattr("gaia.eval.claude.anthropic", _make_mock_anthropic())
    monkeypatch.setattr("gaia.eval.claude.BeautifulSoup", MagicMock())
    from gaia.eval.claude import ClaudeClient

    kwargs = {"model": model}
    if temperature is not None:
        kwargs["temperature"] = temperature
    return ClaudeClient(**kwargs)


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
# Thinking-block-safe text extraction (#3884)
#
# claude-opus-5 (the default judge model) prepends a ThinkingBlock with no
# ``.text`` attribute; earlier code indexed ``message.content[0].text``
# blindly and crashed with AttributeError on the model's very first reply.
# ---------------------------------------------------------------------------


class TestFirstTextSkipsThinkingBlock:
    """analyze_file / analyze_file_with_usage must skip non-text blocks."""

    @pytest.fixture()
    def client(self, monkeypatch):
        return _build_client(monkeypatch, model="claude-opus-5")

    @staticmethod
    def _thinking_then_text(text="the answer"):
        # A real ThinkingBlock has no `.text` attribute at all (not even
        # None) — SimpleNamespace without a `text=` kwarg reproduces that
        # exactly, unlike a Mock() which would auto-vivify one.
        thinking_block = SimpleNamespace(type="thinking", thinking="reasoning...")
        text_block = SimpleNamespace(type="text", text=text)
        return [thinking_block, text_block]

    def test_analyze_file_html_returns_text_block_not_block_zero(
        self, client, tmp_path, monkeypatch
    ):
        html_file = tmp_path / "doc.html"
        html_file.write_text("<html><body>hi</body></html>", encoding="utf-8")
        monkeypatch.setattr(client, "_convert_html_to_text", lambda *a, **kw: "hi")
        client.client.messages.create.return_value = SimpleNamespace(
            content=self._thinking_then_text("summary text")
        )

        result = client.analyze_file(str(html_file), "summarize")

        assert result == "summary text"

    def test_analyze_file_binary_returns_text_block_not_block_zero(
        self, client, tmp_path
    ):
        pdf_file = tmp_path / "doc.pdf"
        pdf_file.write_bytes(b"%PDF-1.4 fake pdf content")
        client.client.messages.create.return_value = SimpleNamespace(
            content=self._thinking_then_text("pdf summary")
        )

        result = client.analyze_file(str(pdf_file), "summarize")

        assert result == "pdf summary"

    def test_analyze_file_with_usage_text_branch_skips_thinking_block(
        self, client, tmp_path
    ):
        txt_file = tmp_path / "doc.txt"
        txt_file.write_text("hello world", encoding="utf-8")
        client.client.messages.create.return_value = SimpleNamespace(
            content=self._thinking_then_text("text branch answer"),
            usage=SimpleNamespace(input_tokens=10, output_tokens=5),
        )

        result = client.analyze_file_with_usage(str(txt_file), "summarize")

        assert result["content"] == "text branch answer"

    def test_analyze_file_with_usage_binary_branch_skips_thinking_block(
        self, client, tmp_path
    ):
        pdf_file = tmp_path / "doc.pdf"
        pdf_file.write_bytes(b"%PDF-1.4 fake pdf content")
        client.client.messages.create.return_value = SimpleNamespace(
            content=self._thinking_then_text("pdf branch answer"),
            usage=SimpleNamespace(input_tokens=10, output_tokens=5),
        )

        result = client.analyze_file_with_usage(str(pdf_file), "summarize")

        assert result["content"] == "pdf branch answer"

    def test_no_text_block_raises_actionable_error_naming_model_and_types(
        self, client, tmp_path
    ):
        txt_file = tmp_path / "doc.txt"
        txt_file.write_text("hello world", encoding="utf-8")
        # Only a thinking block — no text block anywhere in the response.
        client.client.messages.create.return_value = SimpleNamespace(
            content=[SimpleNamespace(type="thinking", thinking="reasoning...")],
            usage=SimpleNamespace(input_tokens=10, output_tokens=5),
        )

        with pytest.raises(ValueError) as excinfo:
            client.analyze_file_with_usage(str(txt_file), "summarize")

        message = str(excinfo.value)
        assert "claude-opus-5" in message
        assert "SimpleNamespace" in message


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


# ---------------------------------------------------------------------------
# Judge sampling temperature (#2094 AC-5)
#
# The judge's own sampling must be pinnable via a `temperature` constructor
# kwarg, forwarded to every `messages.create` call site *only* when set —
# leaving it unset must reproduce today's behavior exactly (no `temperature`
# kwarg at all), since a non-judge caller (pdf_document_generator.py) relies
# on the API default for fixture-generation diversity.
# ---------------------------------------------------------------------------


class TestTemperatureStored:
    def test_default_temperature_is_none(self, monkeypatch):
        client = _build_client(monkeypatch)
        assert client.temperature is None

    def test_explicit_temperature_is_stored(self, monkeypatch):
        client = _build_client(monkeypatch, temperature=0.0)
        assert client.temperature == 0.0


class TestGetCompletionTemperature:
    def test_temperature_pinned_when_set(self, monkeypatch):
        client = _build_client(monkeypatch, temperature=0.0)
        client.client.messages.create.return_value = SimpleNamespace(
            content=[SimpleNamespace(text="ok")]
        )
        client.get_completion("prompt")
        kwargs = client.client.messages.create.call_args.kwargs
        assert kwargs["temperature"] == 0.0

    def test_temperature_omitted_by_default(self, monkeypatch):
        client = _build_client(monkeypatch)
        client.client.messages.create.return_value = SimpleNamespace(
            content=[SimpleNamespace(text="ok")]
        )
        client.get_completion("prompt")
        kwargs = client.client.messages.create.call_args.kwargs
        assert "temperature" not in kwargs

    def test_non_default_temperature_is_forwarded(self, monkeypatch):
        """Distinguishes "reads self.temperature" from "hardcodes 0.0 at each
        call site" — both satisfy AC-5's wording, only the former survives a
        future default change."""
        client = _build_client(monkeypatch, temperature=0.7)
        client.client.messages.create.return_value = SimpleNamespace(
            content=[SimpleNamespace(text="ok")]
        )
        client.get_completion("prompt")
        kwargs = client.client.messages.create.call_args.kwargs
        assert kwargs["temperature"] == 0.7


class TestGetCompletionWithUsageTemperature:
    def test_temperature_pinned_when_set(self, monkeypatch):
        client = _build_client(monkeypatch, temperature=0.0)
        client.client.messages.create.return_value = SimpleNamespace(
            content=[SimpleNamespace(text="ok")],
            usage=SimpleNamespace(input_tokens=10, output_tokens=5),
        )
        client.get_completion_with_usage("prompt")
        kwargs = client.client.messages.create.call_args.kwargs
        assert kwargs["temperature"] == 0.0

    def test_temperature_omitted_by_default(self, monkeypatch):
        client = _build_client(monkeypatch)
        client.client.messages.create.return_value = SimpleNamespace(
            content=[SimpleNamespace(text="ok")],
            usage=SimpleNamespace(input_tokens=10, output_tokens=5),
        )
        client.get_completion_with_usage("prompt")
        kwargs = client.client.messages.create.call_args.kwargs
        assert "temperature" not in kwargs


class TestAnalyzeFileHtmlTemperature:
    """analyze_file's HTML branch (claude.py:246)."""

    @pytest.fixture()
    def html_file(self, tmp_path):
        path = tmp_path / "doc.html"
        path.write_text("<html><body>hello</body></html>", encoding="utf-8")
        return path

    def test_temperature_pinned_when_set(self, monkeypatch, html_file):
        client = _build_client(monkeypatch, temperature=0.0)
        monkeypatch.setattr(client, "_convert_html_to_text", lambda *a, **kw: "hello")
        client.client.messages.create.return_value = SimpleNamespace(
            content=[SimpleNamespace(text="analysis")]
        )
        client.analyze_file(str(html_file), "summarize")
        kwargs = client.client.messages.create.call_args.kwargs
        assert kwargs["temperature"] == 0.0

    def test_temperature_omitted_by_default(self, monkeypatch, html_file):
        client = _build_client(monkeypatch)
        monkeypatch.setattr(client, "_convert_html_to_text", lambda *a, **kw: "hello")
        client.client.messages.create.return_value = SimpleNamespace(
            content=[SimpleNamespace(text="analysis")]
        )
        client.analyze_file(str(html_file), "summarize")
        kwargs = client.client.messages.create.call_args.kwargs
        assert "temperature" not in kwargs


class TestAnalyzeFileBinaryTemperature:
    """analyze_file's base64/document branch (claude.py:277)."""

    @pytest.fixture()
    def pdf_file(self, tmp_path):
        path = tmp_path / "doc.pdf"
        path.write_bytes(b"%PDF-1.4 fake pdf content")
        return path

    def test_temperature_pinned_when_set(self, monkeypatch, pdf_file):
        client = _build_client(monkeypatch, temperature=0.0)
        client.client.messages.create.return_value = SimpleNamespace(
            content=[SimpleNamespace(text="analysis")]
        )
        client.analyze_file(str(pdf_file), "summarize")
        kwargs = client.client.messages.create.call_args.kwargs
        assert kwargs["temperature"] == 0.0

    def test_temperature_omitted_by_default(self, monkeypatch, pdf_file):
        client = _build_client(monkeypatch)
        client.client.messages.create.return_value = SimpleNamespace(
            content=[SimpleNamespace(text="analysis")]
        )
        client.analyze_file(str(pdf_file), "summarize")
        kwargs = client.client.messages.create.call_args.kwargs
        assert "temperature" not in kwargs


class TestAnalyzeFileWithUsageTextTemperature:
    """analyze_file_with_usage's text branch (claude.py:342)."""

    @pytest.fixture()
    def txt_file(self, tmp_path):
        path = tmp_path / "doc.txt"
        path.write_text("hello world", encoding="utf-8")
        return path

    def test_temperature_pinned_when_set(self, monkeypatch, txt_file):
        client = _build_client(monkeypatch, temperature=0.0)
        client.client.messages.create.return_value = SimpleNamespace(
            content=[SimpleNamespace(text="analysis")],
            usage=SimpleNamespace(input_tokens=10, output_tokens=5),
        )
        client.analyze_file_with_usage(str(txt_file), "summarize")
        kwargs = client.client.messages.create.call_args.kwargs
        assert kwargs["temperature"] == 0.0

    def test_temperature_omitted_by_default(self, monkeypatch, txt_file):
        client = _build_client(monkeypatch)
        client.client.messages.create.return_value = SimpleNamespace(
            content=[SimpleNamespace(text="analysis")],
            usage=SimpleNamespace(input_tokens=10, output_tokens=5),
        )
        client.analyze_file_with_usage(str(txt_file), "summarize")
        kwargs = client.client.messages.create.call_args.kwargs
        assert "temperature" not in kwargs


class TestAnalyzeFileWithUsageBinaryTemperature:
    """analyze_file_with_usage's binary/PDF branch (claude.py:389)."""

    @pytest.fixture()
    def pdf_file(self, tmp_path):
        path = tmp_path / "doc.pdf"
        path.write_bytes(b"%PDF-1.4 fake pdf content")
        return path

    def test_temperature_pinned_when_set(self, monkeypatch, pdf_file):
        client = _build_client(monkeypatch, temperature=0.0)
        client.client.messages.create.return_value = SimpleNamespace(
            content=[SimpleNamespace(text="analysis")],
            usage=SimpleNamespace(input_tokens=10, output_tokens=5),
        )
        client.analyze_file_with_usage(str(pdf_file), "summarize")
        kwargs = client.client.messages.create.call_args.kwargs
        assert kwargs["temperature"] == 0.0

    def test_temperature_omitted_by_default(self, monkeypatch, pdf_file):
        client = _build_client(monkeypatch)
        client.client.messages.create.return_value = SimpleNamespace(
            content=[SimpleNamespace(text="analysis")],
            usage=SimpleNamespace(input_tokens=10, output_tokens=5),
        )
        client.analyze_file_with_usage(str(pdf_file), "summarize")
        kwargs = client.client.messages.create.call_args.kwargs
        assert "temperature" not in kwargs


# ---------------------------------------------------------------------------
# Judge factories send no sampling params
#
# The judge model rejects `temperature` with a 400, which fails every judged
# eval at its first API call. The factories pinned temperature=0.0 until that
# parameter was removed from the model; these tests keep the pin from coming
# back. Determinism is now a property of the judge prompt, not the request.
# ---------------------------------------------------------------------------


class TestJudgeFactoryTemperature:
    def test_action_item_quality_sends_no_temperature(self, monkeypatch):
        spy = MagicMock()
        monkeypatch.setattr("gaia.eval.claude.ClaudeClient", spy)

        from gaia.eval.action_item_quality import make_claude_judge

        make_claude_judge(model="claude-sonnet-4-6")

        spy.assert_called_once_with(model="claude-sonnet-4-6")

    def test_briefing_quality_sends_no_temperature(self, monkeypatch):
        spy = MagicMock()
        monkeypatch.setattr("gaia.eval.claude.ClaudeClient", spy)

        from gaia.eval.briefing_quality import make_claude_judge

        make_claude_judge(model="claude-sonnet-4-6")

        spy.assert_called_once_with(model="claude-sonnet-4-6")

    def test_draft_quality_sends_no_temperature(self, monkeypatch):
        spy = MagicMock()
        monkeypatch.setattr("gaia.eval.claude.ClaudeClient", spy)

        from gaia.eval.draft_quality import make_claude_judge

        make_claude_judge(model="claude-sonnet-4-6")

        spy.assert_called_once_with(model="claude-sonnet-4-6")
