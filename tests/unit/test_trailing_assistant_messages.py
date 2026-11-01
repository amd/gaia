# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

from gaia.llm.lemonade_client import normalize_trailing_assistant_messages


def test_coalesces_only_a_trailing_assistant_run_without_mutating_input():
    messages = [
        {"role": "user", "content": "question"},
        {"role": "assistant", "content": "first reply"},
        {"role": "assistant", "content": "recovery note"},
    ]

    normalized = normalize_trailing_assistant_messages(messages)

    assert normalized == [
        {"role": "user", "content": "question"},
        {"role": "assistant", "content": "first reply\n\nrecovery note"},
    ]
    assert messages[-1]["content"] == "recovery note"


def test_preserves_native_tool_calls_while_coalescing():
    calls = [{"id": "call-1", "function": {"name": "search", "arguments": "{}"}}]
    messages = [
        {"role": "user", "content": "question"},
        {"role": "assistant", "content": None, "tool_calls": calls},
        {"role": "assistant", "content": "I will use the result next."},
    ]

    normalized = normalize_trailing_assistant_messages(messages)

    assert normalized[-1]["tool_calls"] == calls
    assert normalized[-1]["content"] == "I will use the result next."
