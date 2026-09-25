# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""No history turn may ever reach a backend as the literal word "None".

Measured on a 144-run battery: a tool-call turn replayed as "None" and one
model started answering "None" back. #3997 removed the text flattening that
produced it for assistant turns; a ``None`` content on any other turn still
stringified, so the guard lives in ``_normalize_message_content``.
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


def test_none_content_never_becomes_the_word_none():
    assert _sdk(False)._normalize_message_content(None) == ""


def test_tool_result_with_no_content_is_empty_not_none():
    out = _sdk(False)._structure_history_message(
        {"role": "tool", "name": "edit_file", "content": None, "tool_call_id": "c1"}
    )
    assert out["content"] == ""


def test_plain_turn_with_no_content_is_empty_not_none():
    out = _sdk(False)._structure_history_message({"role": "assistant", "content": None})
    assert out == {"role": "assistant", "content": ""}


def test_tool_call_turn_stays_native_with_null_content():
    """``content: None`` beside ``tool_calls`` is the wire shape, not the string."""
    out = _sdk(False)._structure_history_message(
        {"role": "assistant", "content": None, "tool_calls": OPENAI_CALL}
    )
    assert out["tool_calls"] == OPENAI_CALL
    assert out["content"] is None


def test_text_alongside_a_tool_call_is_kept_verbatim():
    out = _sdk(False)._structure_history_message(
        {"role": "assistant", "content": "Renaming now.", "tool_calls": OPENAI_CALL}
    )
    assert out["content"] == "Renaming now."
    assert out["tool_calls"] == OPENAI_CALL


def test_an_unpaired_tool_result_still_names_the_tool():
    out = AgentSDK._tool_result_as_text(
        {"role": "tool", "name": "edit_file", "content": '{"status": "success"}'},
        "edit_file",
    )
    assert out["role"] == "user"
    assert out["content"].startswith("[Tool result: edit_file]")


def test_claude_path_still_sends_native_tool_calls():
    out = _sdk(True)._structure_history_message(
        {"role": "assistant", "content": None, "tool_calls": OPENAI_CALL}
    )
    assert out["tool_calls"] == OPENAI_CALL and out["content"] is None
