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
        logger.warning(
            "No context budget remains for history; replaying none of the %d "
            "stored turn(s). Full evidence remains in the session database.",
            len(turns),
        )
        return []
    retained: deque = deque()
    used = 0
    evicted = 0
    unreplayable = 0
    hole = False
    for turn in turns:
        cost = count_tokens(json.dumps(turn, ensure_ascii=False))
        if cost > budget:
            hole = True
            unreplayable += 1
            continue
        if hole:
            # Replay stays contiguous: nothing before a dropped turn may sit
            # next to what followed it.
            evicted += len(retained)
            retained.clear()
            used = 0
            hole = False
        retained.append((turn, cost))
        used += cost
        if used > budget:
            target = budget // 2
            while len(retained) > 1 and used > target:
                _, removed = retained.popleft()
                used -= removed
                evicted += 1
    if evicted or unreplayable:
        logger.warning(
            "Context budget excluded %d older turn(s) and %d turn(s) larger than "
            "the whole history budget; full evidence remains in the session "
            "database (history budget: %d estimated tokens).",
            evicted,
            unreplayable,
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


def text_tool_history(turns: list[list[dict]]) -> list[list[dict]]:
    """Render recorded native tool evidence for a model without native tools."""
    result = deepcopy(turns)
    for turn in result:
        for index, message in enumerate(turn):
            if message.get("tool_calls"):
                calls = json.dumps(message["tool_calls"], ensure_ascii=False)
                content = message.get("content") or ""
                if not isinstance(content, str):
                    content = json.dumps(content, ensure_ascii=False)
                turn[index] = {
                    "role": "assistant",
                    "content": content + "\n[Recorded tool calls] " + calls,
                }
            elif message.get("role") == "tool":
                content = message.get("content")
                turn[index] = {
                    "role": "user",
                    "content": "[Recorded tool result: "
                    + message.get("name", "unknown")
                    + "] "
                    + (
                        content
                        if isinstance(content, str)
                        else json.dumps(content, ensure_ascii=False)
                    ),
                }
    return result


def history_budget(agent: Any, query: str) -> int:
    """Reserve prompt, tools, output and safety margin for any transport."""
    from gaia.llm.lemonade_client import (
        GPU_CTX_SIZE,
        LemonadeClient,
        cloud_model_provider,
        resolve_ctx_size,
    )

    model = getattr(agent, "model_id", None)
    provider = getattr(getattr(agent, "chat", None), "llm_client", None)
    backend = getattr(provider, "_backend", None)
    cloud = (
        backend.cloud_model_provider(model)
        if isinstance(backend, LemonadeClient)
        else cloud_model_provider(model)
    )
    if getattr(agent, "_use_claude", False):
        from gaia.llm.providers.claude import CLAUDE_CTX_SIZE

        ctx = CLAUDE_CTX_SIZE
    elif cloud:
        # Remote admission policy, not a claim about the provider's context ceiling.
        ctx = GPU_CTX_SIZE
    else:
        ctx = resolve_ctx_size(
            model=getattr(agent, "model_id", None),
            device=getattr(agent, "device", None),
        )
    prompt = getattr(agent, "system_prompt", "")
    tools = getattr(agent, "_openai_tools", [])
    overhead = count_tokens(json.dumps([prompt, tools, query], ensure_ascii=False))
    config = getattr(getattr(agent, "chat", None), "config", None)
    output = getattr(config, "max_tokens", 8192)
    return min(ctx // 2, ctx - overhead - output - 2048)


class SessionHistory(list):
    """A bounded model view backed by a complete, optionally durable transcript.

    ``clear`` also erases the archive so clearing a TUI session cannot resurrect
    earlier evidence. Each append is committed before the next turn starts.
    """

    def __init__(self, path=None):
        import sqlite3
        from pathlib import Path

        super().__init__()
        if path is not None:
            path = Path(path)
            path.parent.mkdir(parents=True, exist_ok=True)
            # Transcripts contain local file contents; keep new archives private.
            import os

            fd = (
                os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
                if not path.exists()
                else None
            )
            if fd is not None:
                os.close(fd)
        self._db = sqlite3.connect(str(path) if path is not None else ":memory:")
        self._db.execute(
            "CREATE TABLE IF NOT EXISTS turns (id INTEGER PRIMARY KEY, messages TEXT NOT NULL)"
        )
        self._db.commit()

    def record(self, messages: list[dict]) -> None:
        """Persist a complete turn without modifying its native tool IDs."""
        with self._db:
            self._db.execute(
                "INSERT INTO turns(messages) VALUES (?)",
                (json.dumps(messages, ensure_ascii=False),),
            )

    def prepare(self, agent: Any, query: str) -> None:
        """Rebuild the bounded view for the currently selected model."""
        turns = [
            json.loads(row[0])
            for row in self._db.execute("SELECT messages FROM turns ORDER BY id")
        ]
        if (
            hasattr(agent, "_uses_native_tool_calls")
            and not agent._uses_native_tool_calls()
        ):
            turns = text_tool_history(turns)
        self[:] = select_history(turns, history_budget(agent, query))

    def clear(self) -> None:
        with self._db:
            self._db.execute("DELETE FROM turns")
        super().clear()

    def close(self) -> None:
        self._db.close()
