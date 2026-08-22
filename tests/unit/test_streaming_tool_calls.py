# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""The seam that lets a tool-calling agent stream its answer.

A tool-capable agent always sends a tools array, so until the provider learned
to reassemble streamed ``tool_calls`` fragments, every turn it ran was silent
from the first word to the last. Three layers have to agree for that to work:

    lemonade_client._stream_chat_chunks  forwards the tool_call delta frames
    LemonadeProvider._handle_stream      rebuilds them into the sentinel envelope
    AgentSDK.send_messages_stream        tells that envelope apart from prose

The last one is what this file covers; the middle one lives in
``tests/unit/test_tool_call_priority.py``.
"""

import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from gaia.chat.sdk import AgentConfig, AgentSDK
from gaia.llm.lemonade_client import _tool_call_deltas
from gaia.llm.providers.lemonade import NATIVE_TOOL_CALLS_PREFIX

TOOLS = [{"type": "function", "function": {"name": "search_docs"}}]

SENTINEL = json.dumps(
    {
        "__tool_calls__": [
            {"id": "call_1", "function": {"name": "search_docs", "arguments": "{}"}}
        ],
        "finish_reason": "tool_calls",
        "content": None,
    }
)


def _sdk(chunks):
    """An AgentSDK whose LLM client streams exactly ``chunks``."""
    with patch("gaia.chat.sdk.create_client") as create:
        client = MagicMock()
        client.chat.return_value = iter(chunks)
        create.return_value = client
        sdk = AgentSDK(config=AgentConfig())
    sdk.get_stats = lambda: {}
    return sdk, client


def test_sentinel_terminates_the_stream_and_is_never_prose():
    """The envelope is a control frame — showing it would print raw JSON."""
    sdk, _ = _sdk(["Let me check.", SENTINEL])
    responses = list(
        sdk.send_messages_stream([{"role": "user", "content": "q"}], tools=TOOLS)
    )

    assert [r.text for r in responses] == ["Let me check.", SENTINEL]
    assert [r.is_complete for r in responses] == [False, True]
    # Exactly one complete response, and it is the last thing yielded: the
    # agent loop reads is_complete + non-empty text as "this turn called a tool".
    assert sum(r.is_complete for r in responses) == 1


def test_plain_stream_yields_incremental_chunks_then_an_empty_terminator():
    sdk, _ = _sdk(["An NPU ", "is a chip."])
    responses = list(
        sdk.send_messages_stream([{"role": "user", "content": "q"}], tools=TOOLS)
    )

    assert [r.text for r in responses] == ["An NPU ", "is a chip.", ""]
    assert [r.is_complete for r in responses] == [False, False, True]


def test_tools_are_omitted_from_the_call_when_absent():
    """Non-Lemonade providers take kwargs straight to their own API, which
    rejects an OpenAI-shaped tools array — so a bare stream must not carry one."""
    sdk, client = _sdk(["hi"])
    list(sdk.send_messages_stream([{"role": "user", "content": "q"}]))

    assert "tools" not in client.chat.call_args.kwargs


def test_a_lone_sentinel_lookalike_without_tools_stays_prose():
    """Without a tools array there is no envelope to expect, so text that merely
    looks like one is still text — a model quoting the sentinel must not end the turn.
    """
    sdk, _ = _sdk([NATIVE_TOOL_CALLS_PREFIX + " ..."])
    responses = list(sdk.send_messages_stream([{"role": "user", "content": "q"}]))

    assert [r.is_complete for r in responses] == [False, True]


def test_tool_call_deltas_ignores_a_non_sequence():
    """A test double's auto-created attribute must not reach the accumulator."""
    assert _tool_call_deltas(MagicMock()) is None
    assert _tool_call_deltas(SimpleNamespace()) is None
    assert _tool_call_deltas(SimpleNamespace(tool_calls=[])) is None


def test_tool_call_deltas_unpacks_pydantic_and_plain_fragments():
    fragment = SimpleNamespace(model_dump=lambda: {"index": 0, "id": "call_1"})
    assert _tool_call_deltas(SimpleNamespace(tool_calls=[fragment])) == [
        {"index": 0, "id": "call_1"}
    ]
    assert _tool_call_deltas(SimpleNamespace(tool_calls=[{"index": 1}])) == [
        {"index": 1}
    ]
