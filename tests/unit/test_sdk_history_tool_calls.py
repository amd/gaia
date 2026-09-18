# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""What a text-history backend (local, Fireworks) sees for a tool-call turn.

Two ways this has gone wrong, both measured on the same 144-run battery:
the turn replayed as the word "None" (one model began answering "None"), and
the turn replayed as ``[Tool call: name(args)]`` text (models copied that
format instead of calling tools natively, and 58 runs died on parse errors).
The turn must carry neither.
"""

import json
from types import SimpleNamespace

from gaia.chat.sdk import AgentSDK

OPENAI_CALL = [
    {
        "id": "c1",
        "type": "function",
        "function": {
            "name": "edit_file",
            "arguments": json.dumps({"file_path": "toybox/dates.py"}),
        },
    }
]


def _sdk(use_claude):
    sdk = AgentSDK.__new__(AgentSDK)
    sdk.config = SimpleNamespace(use_claude=use_claude)
    return sdk


def test_tool_call_turn_is_neither_none_nor_imitable_text():
    out = _sdk(False)._structure_history_message(
        {"role": "assistant", "content": None, "tool_calls": OPENAI_CALL}
    )
    assert out == {"role": "assistant", "content": ""}


def test_text_alongside_a_tool_call_is_kept_verbatim():
    out = _sdk(False)._structure_history_message(
        {"role": "assistant", "content": "Renaming now.", "tool_calls": OPENAI_CALL}
    )
    assert out == {"role": "assistant", "content": "Renaming now."}


def test_tool_result_still_names_the_tool():
    out = _sdk(False)._structure_history_message(
        {"role": "tool", "name": "edit_file", "content": '{"status": "success"}'}
    )
    assert out["role"] == "user"
    assert out["content"].startswith("[Tool result: edit_file]")


def test_claude_path_still_sends_native_tool_calls():
    out = _sdk(True)._structure_history_message(
        {"role": "assistant", "content": None, "tool_calls": OPENAI_CALL}
    )
    assert out["tool_calls"] == OPENAI_CALL and out["content"] is None


def test_none_content_never_becomes_the_word_none():
    assert _sdk(False)._normalize_message_content(None) == ""
