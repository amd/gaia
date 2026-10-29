# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Real-Anthropic check that the rolling breakpoint caches conversation history.

The unit tests assert the request shape; only the live API can confirm the
second call of a conversation actually reads the first call's history from
cache. No system prompt and no tools, so the rolling marker is the only one.
"""

import os
import uuid

import pytest


@pytest.mark.integration
def test_second_call_reads_the_first_calls_history_from_cache():
    from gaia.env import load_env

    load_env()
    # Placeholder keys (CI sets dummy values) would 401 rather than skip.
    if not os.getenv("ANTHROPIC_API_KEY", "").startswith("sk-ant-"):
        pytest.skip("no real ANTHROPIC_API_KEY set")
    pytest.importorskip("anthropic")
    from gaia.llm.providers.claude import ClaudeProvider

    provider = ClaudeProvider()
    # The nonce forces a cold cache, so call 1 must write rather than read a
    # leftover entry. ~7K tokens clears every model's minimum cacheable length.
    facts = "\n".join(
        f"Fact {n}: item {n} is filed under shelf {n * 7 % 113}." for n in range(600)
    )
    history = [
        {
            "role": "user",
            "content": f"Run {uuid.uuid4()}. Memorise these facts.\n{facts}\n"
            "Reply with just OK.",
        }
    ]

    reply = provider.chat(history, max_tokens=16)
    first = provider.get_last_usage()
    assert first["cache_creation_input_tokens"] > 0, first

    history += [
        {"role": "assistant", "content": reply or "OK"},
        {"role": "user", "content": "Which shelf holds item 42? Number only."},
    ]
    provider.chat(history, max_tokens=16)
    second = provider.get_last_usage()

    assert second["cache_read_input_tokens"] >= first["cache_creation_input_tokens"], (
        first,
        second,
    )
