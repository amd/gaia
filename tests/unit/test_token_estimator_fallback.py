# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Without its tokenizer data the counter says so and overcounts (#5066).

History trimming and context eviction take this count as a size. A fallback
that undercounts JSON or CJK text lets a prompt that looks like half the
window fill all of it.
"""

# pylint: disable=protected-access

import json
import logging

import pytest

from gaia.agents.base import turn_metrics


@pytest.fixture
def no_tiktoken(monkeypatch):
    import tiktoken

    def _fail(_name):
        raise OSError("cannot download cl100k_base: proxy unreachable")

    monkeypatch.setattr(tiktoken, "get_encoding", _fail)
    monkeypatch.setattr(turn_metrics, "_TOKENIZER", None)
    yield
    turn_metrics._TOKENIZER = None


def test_the_fallback_is_named_at_warning_level(no_tiktoken, caplog):
    with caplog.at_level(logging.WARNING, logger=turn_metrics.__name__):
        turn_metrics.count_tokens("hello")
    assert "cl100k_base" in caplog.text
    assert "overcount" in caplog.text


@pytest.mark.parametrize(
    "text",
    [
        json.dumps([{"id": i, "ok": True, "tags": ["a", "b"]} for i in range(200)]),
        "日本語のテキストです。" * 200,
        "plain prose with ordinary words in it " * 200,
    ],
    ids=["json", "cjk", "prose"],
)
def test_the_fallback_never_counts_less_than_utf8_bytes_over_three(no_tiktoken, text):
    assert turn_metrics.count_tokens(text) >= len(text.encode("utf-8")) / 3


def test_the_real_tokenizer_is_used_when_it_loads(monkeypatch):
    monkeypatch.setattr(turn_metrics, "_TOKENIZER", None)
    assert turn_metrics._tokenizer().name == "tiktoken:cl100k_base"
    monkeypatch.setattr(turn_metrics, "_TOKENIZER", None)


@pytest.mark.parametrize(
    "text",
    ["bad \udc80 bytes " * 50, "\ud83d alone", "emoji 🚀🚀🚀 " * 50],
    ids=["lone-low-surrogate", "lone-high-surrogate", "emoji"],
)
def test_lone_surrogates_and_astral_text_are_counted_not_raised(text):
    """Leniently decoded binary output reaches the counter; it must not crash a turn."""
    assert turn_metrics.count_tokens(text) > 0


@pytest.mark.parametrize(
    "text",
    ["bad \udc80 bytes " * 50, "emoji 🚀🚀🚀 " * 50],
    ids=["lone-surrogate", "emoji"],
)
def test_the_fallback_counts_surrogates_and_astral_text_high(no_tiktoken, text):
    size = len(text.encode("utf-8", "surrogatepass"))
    assert turn_metrics.count_tokens(text) >= size / 3


def test_the_fallback_counts_nothing_as_zero(no_tiktoken):
    assert turn_metrics.count_tokens("") == 0
