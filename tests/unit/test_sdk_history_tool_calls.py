# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""A tool-call turn must survive into the history a text-history backend receives.

Before this, every such turn reached a local or Fireworks model as an assistant
message saying "None", with the call itself dropped — so the model never saw
what it had called, and one model began answering "None".
"""

import json
from types import SimpleNamespace

import pytest

from gaia.chat.sdk import AgentSDK

OPENAI_CALL = [
    {
        "id": "c1",
        "type": "function",
        "function": {
            "name": "edit_file",
            "arguments": json.dumps(
                {"file_path": "toybox/dates.py", "old_content": "a", "new_content": "b"}
            ),
        },
    }
]


def _sdk(use_claude):
    sdk = AgentSDK.__new__(AgentSDK)
    sdk.config = SimpleNamespace(use_claude=use_claude)
    return sdk


def test_tool_call_turn_is_not_replayed_as_none():
    out = _sdk(False)._structure_history_message(
        {"role": "assistant", "content": None, "tool_calls": OPENAI_CALL}
    )
    assert out["role"] == "assistant"
    assert "None" not in out["content"]
    assert out["content"].startswith("[Tool call: edit_file(")
    assert (
        "toybox/dates.py" in out["content"]
    ), "the arguments are what stop a model repeating itself"


def test_text_alongside_a_tool_call_is_kept():
    out = _sdk(False)._structure_history_message(
        {"role": "assistant", "content": "Renaming now.", "tool_calls": OPENAI_CALL}
    )
    assert out["content"].startswith("Renaming now.\n[Tool call: edit_file(")


def test_gaia_normalised_tool_call_shape_is_rendered():
    call = [{"id": "c1", "name": "read_file", "tool_args": {"file_path": "a.py"}}]
    out = _sdk(False)._structure_history_message(
        {"role": "assistant", "content": None, "tool_calls": call}
    )
    assert out["content"] == '[Tool call: read_file({"file_path": "a.py"})]'


def test_claude_path_still_sends_native_tool_calls():
    out = _sdk(True)._structure_history_message(
        {"role": "assistant", "content": None, "tool_calls": OPENAI_CALL}
    )
    assert out["tool_calls"] == OPENAI_CALL and out["content"] is None


def test_plain_assistant_message_is_unchanged():
    out = _sdk(False)._structure_history_message(
        {"role": "assistant", "content": "Done."}
    )
    assert out == {"role": "assistant", "content": "Done."}


@pytest.mark.parametrize("content", [None])
def test_none_content_never_becomes_the_word_none(content):
    assert _sdk(False)._normalize_message_content(content) == ""
