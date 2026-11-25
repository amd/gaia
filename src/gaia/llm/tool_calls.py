# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""How an ``LLMClient`` hands native tool calls back to the agent loop.

``LLMClient.chat`` returns a string. A reply that carries tool calls is the
JSON envelope built here, so a client written outside GAIA does not have to
copy the wire format from a provider's source.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from gaia.llm.providers.lemonade import _NATIVE_TC_KEY


def tool_calls_reply(
    tool_calls: List[Dict[str, Any]],
    content: Optional[str] = None,
    finish_reason: Optional[str] = "tool_calls",
) -> str:
    """The string ``chat()`` returns for a reply that calls tools.

    Args:
        tool_calls: OpenAI-shape calls, each
            ``{"id": ..., "type": "function", "function": {"name": ...,
            "arguments": "<JSON string>"}}``.
        content: Text the model wrote beside the calls, if any.
        finish_reason: Why the reply ended. ``"length"`` tells the loop the
            output cap cut the call off, so it asks for the call again.
    """
    if not tool_calls:
        raise ValueError(
            "tool_calls_reply() needs at least one tool call; return the "
            "model's text itself for a reply without any."
        )
    return json.dumps(
        {_NATIVE_TC_KEY: tool_calls, "finish_reason": finish_reason, "content": content}
    )


def tool_call(name: str, arguments: Dict[str, Any], call_id: str) -> Dict[str, Any]:
    """One OpenAI-shape tool call for :func:`tool_calls_reply`."""
    return {
        "id": call_id,
        "type": "function",
        "function": {"name": name, "arguments": json.dumps(arguments)},
    }
