# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Model-facing conversation history, budgeted without splitting tool groups."""

import json
from collections import deque
from copy import deepcopy
from typing import Any

from gaia.agents.base.turn_metrics import count_tokens
from gaia.logger import get_logger

logger = get_logger(__name__)


class TurnMessages(list):
    """Keep the original evidence when overflow recovery shrinks model input."""

    def __init__(self):
        super().__init__()
        self.recorded: list[dict] = []

    def append(self, message):
        super().append(message)
        self.recorded.append(deepcopy(message))

    def extend(self, messages):
        for message in messages:
            self.append(message)

    def finish(self, answer: str) -> list[dict]:
        """Close unfinished native calls truthfully before the final answer."""
        result = []
        pending = {}

        def close_pending():
            for call_id, name in pending.items():
                result.append(
                    {
                        "role": "tool",
                        "tool_call_id": call_id,
                        "name": name,
                        "content": "No result was recorded: the run stopped before "
                        "this call completed. Do not assume it succeeded.",
                    }
                )
            pending.clear()

        for message in deepcopy(self.recorded):
            if message.get("role") != "tool":
                close_pending()
            if message.get("tool_calls"):
                pending.update(
                    (call["id"], call["function"]["name"])
                    for call in message["tool_calls"]
                )
            if message.get("role") == "tool":
                call_id = message.get("tool_call_id")
                if call_id not in pending:
                    close_pending()
                    # Legacy plan execution never advertised a native call.
                    message = {
                        "role": "user",
                        "content": "[Recorded tool result: "
                        + message.get("name", "unknown")
                        + "] "
                        + json.dumps(message.get("content"), ensure_ascii=False),
                    }
                else:
                    pending.pop(call_id)

            result.append(message)
        close_pending()
        if (
            result
            and result[-1].get("role") == "assistant"
            and not result[-1].get("tool_calls")
        ):
            result[-1] = {"role": "assistant", "content": answer}
        else:
            result.append({"role": "assistant", "content": answer})
        return result


def select_history(turns: list[list[dict[str, Any]]], budget: int) -> list[dict]:
    """Replay whole turns, evicting to half the estimated budget on overflow.

    Replaying the same prefix makes eviction deterministic across restarts.
    The persistent transcript is never shortened.
    """
    if budget <= 0:
        raise ValueError("No context budget remains for history; shorten the prompt.")
    retained: deque = deque()
    used = 0
    evicted = 0
    for turn in turns:
        cost = count_tokens(json.dumps(turn, ensure_ascii=False))
        retained.append((turn, cost))
        used += cost
        if used > budget:
            target = budget // 2
            while len(retained) > 1 and used > target:
                _, removed = retained.popleft()
                used -= removed
                evicted += 1
            if used > budget:
                retained.clear()
                used = 0
                evicted += 1
    if evicted:
        logger.warning(
            "Context budget excluded %d older turn(s); full evidence remains in "
            "the session database (history budget: %d estimated tokens).",
            evicted,
            budget,
        )
    return [message for turn, _ in retained for message in turn]


def transcript_turns(messages: list[dict]) -> list[list[dict]]:
    """Restore persisted model messages, or text for legacy completed turns."""
    turns = []
    pending = None
    for message in messages:
        role = message.get("role")
        if role == "user":
            pending = {"role": "user", "content": message["content"]}
        elif role in ("assistant", "autonomous"):
            recorded = message.get("model_messages")
            if recorded is not None:
                turns.append(recorded)
            elif role == "assistant" and pending is not None:
                turns.append(
                    [pending, {"role": "assistant", "content": message["content"]}]
                )
            pending = None
    return turns
