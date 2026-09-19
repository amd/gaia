# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""A history turn with no text must never replay as the word "None".

Persisted tool-call turns carry ``content: None``. Stringified, that reached the
model as "None", and one model began answering "None". How tool calls themselves
are replayed is pinned in ``test_sdk_native_tool_history.py``.
"""

from types import SimpleNamespace

from gaia.chat.sdk import AgentSDK


def _sdk():
    sdk = AgentSDK.__new__(AgentSDK)
    sdk.config = SimpleNamespace(use_claude=False)
    return sdk


def test_none_content_never_becomes_the_word_none():
    assert _sdk()._normalize_message_content(None) == ""


def test_a_turn_without_text_replays_as_empty():
    out = _sdk()._structure_history_message({"role": "user", "content": None})
    assert out == {"role": "user", "content": ""}
