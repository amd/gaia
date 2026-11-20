# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Each RAGSDK.query sends only the chunks retrieved for that question (#4736).

AgentSDK keeps the last few exchanges, and the stored user turn is the full
prompt, context included. A later question was therefore answered with the
earlier question's passages still attached.
"""

import json
from unittest.mock import patch

import pytest

from gaia.rag.sdk import RAGSDK, RAGConfig

_FIRST_CHUNK = "ZEBRA-QUARTZ-CHUNK-ALPHA-4736"
_SECOND_CHUNK = "MANGO-VELVET-CHUNK-BETA-4736"


def _snapshot(chunk: str) -> dict:
    return {
        "retrieved_chunks": [chunk],
        "retrieved_indices": [0],
        "scores": [0.9],
        "chunks": [chunk],
        "chunk_to_file": {0: "doc.txt"},
        "indexed_file_count": 1,
    }


def _outgoing_prompt(call) -> str:
    return json.dumps(call.kwargs["messages"])


def test_second_query_prompt_omits_the_first_questions_chunks(tmp_path):
    snapshots = [_snapshot(_FIRST_CHUNK), _snapshot(_SECOND_CHUNK)]

    with (
        patch("gaia.rag.sdk.RAGSDK._check_dependencies"),
        patch("gaia.llm.providers.lemonade.LemonadeClient") as backend_type,
    ):
        backend = backend_type.return_value
        backend.cloud_model_provider.return_value = None
        backend.chat_completions.return_value = {
            "choices": [{"message": {"content": "answer"}, "finish_reason": "stop"}]
        }
        rag = RAGSDK(RAGConfig(cache_dir=str(tmp_path)))
        rag._search_chunks = lambda _question: snapshots.pop(0)

        rag.query("What does the first document say?")
        rag.query("What does the second document say?")

    calls = backend.chat_completions.call_args_list
    assert len(calls) == 2
    first_prompt = _outgoing_prompt(calls[0])
    second_prompt = _outgoing_prompt(calls[1])
    assert _FIRST_CHUNK in first_prompt
    assert _SECOND_CHUNK not in first_prompt
    assert _SECOND_CHUNK in second_prompt
    assert _FIRST_CHUNK not in second_prompt


_THIRD_CHUNK = "OCELOT-AMBER-CHUNK-GAMMA-4736"
_CHAT_TURN = "PRIOR-CHAT-TURN-DELTA-4736"


def _reply(**kwargs):
    if kwargs.get("stream"):
        return iter(
            [{"choices": [{"delta": {"content": "streamed"}, "finish_reason": "stop"}]}]
        )
    return {"choices": [{"message": {"content": "answer"}, "finish_reason": "stop"}]}


def _rag_with_backend(tmp_path, backend_type, snapshots):
    backend = backend_type.return_value
    backend.cloud_model_provider.return_value = None
    backend.chat_completions.side_effect = _reply
    rag = RAGSDK(RAGConfig(cache_dir=str(tmp_path)))
    rag._search_chunks = lambda _question: snapshots.pop(0)
    return rag, backend


def test_three_queries_each_carry_only_their_own_chunks(tmp_path):
    chunks = [_FIRST_CHUNK, _SECOND_CHUNK, _THIRD_CHUNK]
    with (
        patch("gaia.rag.sdk.RAGSDK._check_dependencies"),
        patch("gaia.llm.providers.lemonade.LemonadeClient") as backend_type,
    ):
        rag, backend = _rag_with_backend(
            tmp_path, backend_type, [_snapshot(c) for c in chunks]
        )
        for i in range(3):
            rag.query(f"Question {i}?")

    prompts = [_outgoing_prompt(c) for c in backend.chat_completions.call_args_list]
    assert len(prompts) == 3
    for own, prompt in zip(chunks, prompts):
        assert [c for c in chunks if c in prompt] == [own]
    assert list(rag.chat.chat_history) == []


def test_query_after_streamed_chat_turn_ignores_and_keeps_that_turn(tmp_path):
    with (
        patch("gaia.rag.sdk.RAGSDK._check_dependencies"),
        patch("gaia.llm.providers.lemonade.LemonadeClient") as backend_type,
    ):
        rag, backend = _rag_with_backend(
            tmp_path, backend_type, [_snapshot(_FIRST_CHUNK)]
        )
        list(rag.chat.send_stream(_CHAT_TURN))
        history_before = list(rag.chat.chat_history)
        rag.query("What does the document say?")

    assert any(_CHAT_TURN in turn for turn in history_before)
    query_prompt = _outgoing_prompt(backend.chat_completions.call_args_list[-1])
    assert _FIRST_CHUNK in query_prompt
    assert _CHAT_TURN not in query_prompt
    assert list(rag.chat.chat_history) == history_before


def test_conversational_send_still_uses_stored_history(tmp_path):
    with (
        patch("gaia.rag.sdk.RAGSDK._check_dependencies"),
        patch("gaia.llm.providers.lemonade.LemonadeClient") as backend_type,
    ):
        rag, backend = _rag_with_backend(
            tmp_path, backend_type, [_snapshot(_FIRST_CHUNK)]
        )
        rag.chat.send(_CHAT_TURN)
        rag.query("What does the document say?")
        rag.chat.send("And what did I say first?")

    follow_up = _outgoing_prompt(backend.chat_completions.call_args_list[-1])
    assert _CHAT_TURN in follow_up
    assert _FIRST_CHUNK not in follow_up


def test_failed_query_leaves_history_untouched(tmp_path):
    with (
        patch("gaia.rag.sdk.RAGSDK._check_dependencies"),
        patch("gaia.llm.providers.lemonade.LemonadeClient") as backend_type,
    ):
        rag, backend = _rag_with_backend(
            tmp_path, backend_type, [_snapshot(_FIRST_CHUNK)]
        )
        rag.chat.send(_CHAT_TURN)
        history_before = list(rag.chat.chat_history)
        backend.chat_completions.side_effect = RuntimeError("backend down")
        with pytest.raises(RuntimeError):
            rag.query("What does the document say?")

    assert list(rag.chat.chat_history) == history_before
