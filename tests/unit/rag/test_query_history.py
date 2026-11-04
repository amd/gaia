# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Each RAGSDK.query sends only the chunks retrieved for that question (#4736).

AgentSDK keeps the last few exchanges, and the stored user turn is the full
prompt, context included. A later question was therefore answered with the
earlier question's passages still attached.
"""

import json
from unittest.mock import patch

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
