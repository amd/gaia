# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Tests for ``gaia.coder.review._llm.call_opus`` after the CoderLLM seam refactor.

The review-pass tests in ``test_review.py`` patch ``call_opus`` wholesale, so
they don't exercise the function's body. These tests pin the contract that
``call_opus`` constructs a :class:`gaia.coder.llm.CoderLLM` and forwards the
arguments through ``complete()`` — i.e. the coder has a single Anthropic
seam (see ``src/gaia/coder/llm.py`` module docstring).
"""

from __future__ import annotations

from typing import Any, List
from unittest.mock import MagicMock

import pytest

from gaia.coder.review._llm import LLMClientUnavailable, call_opus


class _StubCoderLLM:
    """Drop-in stub for :class:`gaia.coder.llm.CoderLLM`.

    Records construction args and ``complete`` kwargs so the test can assert
    the call shape without touching the Anthropic SDK.
    """

    init_calls: List[dict] = []
    complete_calls: List[dict] = []
    next_response: str = "stubbed response"

    def __init__(self, **kwargs: Any) -> None:
        type(self).init_calls.append(kwargs)

    def complete(self, prompt: str, **kwargs: Any) -> str:
        type(self).complete_calls.append({"prompt": prompt, **kwargs})
        return type(self).next_response


@pytest.fixture(autouse=True)
def _reset_stub() -> None:
    _StubCoderLLM.init_calls = []
    _StubCoderLLM.complete_calls = []
    _StubCoderLLM.next_response = "stubbed response"


def test_call_opus_routes_through_coder_llm(mocker) -> None:
    """``call_opus`` MUST construct CoderLLM and forward to ``complete``."""
    mocker.patch("gaia.coder.llm.CoderLLM", _StubCoderLLM)
    _StubCoderLLM.next_response = "OK from stub"
    result = call_opus("hello", model="claude-opus-4-7-x", max_tokens=999)
    assert result == "OK from stub"
    # Constructed once with the model + max_tokens we passed.
    assert len(_StubCoderLLM.init_calls) == 1
    init = _StubCoderLLM.init_calls[0]
    assert init["model"] == "claude-opus-4-7-x"
    assert init["max_tokens"] == 999
    # complete() called once with the prompt + per-call overrides.
    assert len(_StubCoderLLM.complete_calls) == 1
    call = _StubCoderLLM.complete_calls[0]
    assert call["prompt"] == "hello"
    assert call["model"] == "claude-opus-4-7-x"
    assert call["max_tokens"] == 999
    assert call["temperature"] == 0.0


def test_call_opus_defaults_match_review_spec(mocker) -> None:
    """When the caller omits overrides, §15.8 defaults apply."""
    mocker.patch("gaia.coder.llm.CoderLLM", _StubCoderLLM)
    call_opus("p")
    init = _StubCoderLLM.init_calls[0]
    call = _StubCoderLLM.complete_calls[0]
    # Defaults from gaia.coder.review._llm — temperature=0, 1500 max_tokens.
    assert init["max_tokens"] == 1500
    assert call["temperature"] == 0.0
    assert call["max_tokens"] == 1500


def test_call_opus_raises_llm_client_unavailable_on_import_error(mocker) -> None:
    """Anthropic SDK missing → wrapped as :class:`LLMClientUnavailable`."""

    def _raise(**_kwargs: Any) -> None:
        raise ImportError("anthropic not installed")

    mocker.patch("gaia.coder.llm.CoderLLM", side_effect=_raise)
    with pytest.raises(LLMClientUnavailable, match="anthropic SDK not installed"):
        call_opus("x")


def test_call_opus_forwards_system_prompt(mocker) -> None:
    """Optional ``system`` arg must round-trip through ``complete``."""
    mocker.patch("gaia.coder.llm.CoderLLM", _StubCoderLLM)
    call_opus("p", system="you are an architecture reviewer")
    call = _StubCoderLLM.complete_calls[0]
    assert call["system"] == "you are an architecture reviewer"


def test_call_opus_returns_real_string(mocker) -> None:
    """Smoke test: the seam returns whatever CoderLLM.complete returns."""
    fake = MagicMock()
    fake.complete.return_value = "deterministic"
    mocker.patch("gaia.coder.llm.CoderLLM", return_value=fake)
    assert call_opus("anything") == "deterministic"
    fake.complete.assert_called_once()
