# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Failed turns restore the complete bounded conversation, including evicted entries."""

from collections import deque
from unittest.mock import Mock

import pytest

from gaia.chat.sdk import AgentConfig, AgentSDK


@pytest.fixture
def sdk():
    chat = AgentSDK.__new__(AgentSDK)
    chat.config = AgentConfig(
        max_tokens=100,
        temperature=None,
        assistant_name="assistant",
        show_stats=False,
        model="test-model",
        system_prompt="",
    )
    chat.chat_history = deque(["user: first", "assistant: answer"], maxlen=2)
    chat.log = Mock()
    chat.llm_client = Mock()
    chat.rag_enabled = True
    chat._enhance_with_rag = Mock(return_value=("private retrieved passages", {}))
    chat._format_history_for_context = Mock(return_value="formatted prompt")
    return chat


@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("failure", ["format", "generate"])
def test_failed_turn_restores_full_history(sdk, stream, failure):
    original = sdk.chat_history
    before = list(original)
    target = (
        sdk._format_history_for_context
        if failure == "format"
        else sdk.llm_client.generate
    )
    target.side_effect = ValueError("request failed")
    with pytest.raises(ValueError, match="request failed"):
        if stream:
            list(sdk.send_stream("new question"))
        else:
            sdk.send("new question")
    assert sdk.chat_history is original
    assert sdk.chat_history.maxlen == 2
    assert list(sdk.chat_history) == before


def test_cancelled_stream_restores_evicted_history(sdk):
    sdk.llm_client.generate.return_value = iter(["partial", "remaining"])
    stream = sdk.send_stream("new question")
    assert next(stream).text == "partial"
    stream.close()
    assert list(sdk.chat_history) == ["user: first", "assistant: answer"]


@pytest.mark.parametrize("stream", [False, True])
def test_success_records_original_user_text_and_complete_reply(sdk, stream):
    sdk.llm_client.generate.return_value = iter(["one", "two"]) if stream else "onetwo"
    if stream:
        responses = list(sdk.send_stream("new question", include_history=True))
        response = responses[-1]
        assert response.is_complete
    else:
        response = sdk.send("new question", include_history=True)
    assert response.history == ["user: new question", "assistant: onetwo"]
    assert list(sdk.chat_history) == response.history


def test_backend_error_after_stream_chunk_restores_history(sdk):
    def failing_stream():
        yield "partial"
        raise OSError("connection reset")

    sdk.llm_client.generate.return_value = failing_stream()
    stream = sdk.send_stream("new question")
    assert next(stream).text == "partial"
    with pytest.raises(OSError, match="connection reset"):
        next(stream)
    assert list(sdk.chat_history) == ["user: first", "assistant: answer"]
