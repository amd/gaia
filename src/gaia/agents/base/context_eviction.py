# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Evict stale tool results from the context re-sent to the model.

Not compaction: nothing is summarised or dropped. An evicted result is
archived whole in the agent's :class:`~gaia.agents.base.artifacts.ArtifactStore`
and its message in the *sent* list becomes a one-line stub naming the handle
that fetches it; the turn's conversation log is never touched. Off by default.

Every eviction changes the prompt prefix, so the next call re-reads everything
after it uncached once. The policy therefore fires rarely: only when the last
measured prompt is over ``threshold_tokens``, only for results older than
``keep_steps`` steps, and only when those add up to ``min_batch_tokens`` --
then all of them go in one batch. Once evicted, a result stays evicted, so the
prefix is deterministic afterwards.
"""

from __future__ import annotations

import json
import math
import os
from typing import Any, Dict, List, Optional, Tuple

from gaia.llm.cache_pricing import AUTO_EVICTION_MIN_RATIO, cached_input_price_ratio
from gaia.logger import get_logger

logger = get_logger(__name__)

CONTEXT_EVICTION_ENV_VAR = "GAIA_CONTEXT_EVICTION"
EVICT_THRESHOLD_ENV_VAR = "GAIA_EVICT_THRESHOLD"
EVICT_KEEP_ENV_VAR = "GAIA_EVICT_KEEP"
EVICT_MIN_BATCH_ENV_VAR = "GAIA_EVICT_MIN_BATCH"

CONTEXT_EVICTION_MODES = ("off", "on", "auto")
DEFAULT_EVICT_THRESHOLD_TOKENS = 60000
DEFAULT_EVICT_KEEP_STEPS = 8
DEFAULT_EVICT_MIN_BATCH_TOKENS = 30000

#: Token estimate for text the backend has not measured.
CHARS_PER_TOKEN = 3.6

#: Their results are bounded pages or the parent's only view of a worker.
NEVER_EVICTED_TOOLS = frozenset({"read_tool_output", "delegate_task"})

_SUMMARY_VALUE_CHARS = 40
_SUMMARY_CHARS = 100


def context_eviction_from_env() -> Optional[str]:
    """``GAIA_CONTEXT_EVICTION`` as a mode, or ``None`` when unset; malformed fails loudly."""
    raw = os.getenv(CONTEXT_EVICTION_ENV_VAR)
    if raw is None:
        return None
    value = raw.strip().lower()
    if value in CONTEXT_EVICTION_MODES:
        return value
    raise ValueError(
        f"{CONTEXT_EVICTION_ENV_VAR} must be one of "
        f"{', '.join(CONTEXT_EVICTION_MODES)}, got {raw!r}"
    )


def _positive_int_from_env(name: str) -> Optional[int]:
    raw = os.getenv(name)
    if raw is None:
        return None
    try:
        value = int(raw)
    except ValueError as e:
        raise ValueError(f"{name} must be an integer, got {raw!r}") from e
    if value < 1:
        raise ValueError(f"{name} must be at least 1, got {value}")
    return value


def evict_threshold_from_env() -> Optional[int]:
    """``GAIA_EVICT_THRESHOLD`` parsed, or ``None``; malformed fails loudly."""
    return _positive_int_from_env(EVICT_THRESHOLD_ENV_VAR)


def evict_keep_from_env() -> Optional[int]:
    """``GAIA_EVICT_KEEP`` parsed, or ``None``; malformed fails loudly."""
    return _positive_int_from_env(EVICT_KEEP_ENV_VAR)


def evict_min_batch_from_env() -> Optional[int]:
    """``GAIA_EVICT_MIN_BATCH`` parsed, or ``None``; malformed fails loudly."""
    return _positive_int_from_env(EVICT_MIN_BATCH_ENV_VAR)


def resolve_context_eviction(mode: str, model_id: Optional[str], cloud: bool) -> bool:
    """Whether eviction runs: ``on``/``off`` as said, ``auto`` by the price table.

    ``auto`` is on only for a cloud model whose cached/uncached input price
    ratio is known and at least :data:`AUTO_EVICTION_MIN_RATIO`; a local or
    unknown model is off, said once at info level rather than guessed at.
    """
    if mode not in CONTEXT_EVICTION_MODES:
        raise ValueError(
            f"context_eviction must be one of {', '.join(CONTEXT_EVICTION_MODES)}, "
            f"got {mode!r}"
        )
    if mode != "auto":
        return mode == "on"
    if not cloud:
        logger.info("context_eviction=auto: off for local model %s", model_id)
        return False
    ratio = cached_input_price_ratio(model_id)
    if ratio is None:
        logger.info(
            "context_eviction=auto: off, no cached-input price ratio known for %s",
            model_id,
        )
        return False
    enabled = ratio >= AUTO_EVICTION_MIN_RATIO
    logger.info(
        "context_eviction=auto: %s for %s (cached/uncached input price %.2f)",
        "on" if enabled else "off",
        model_id,
        ratio,
    )
    return enabled


def estimate_tokens(text: str) -> int:
    return math.ceil(len(text) / CHARS_PER_TOKEN)


def _message_text(message: Dict[str, Any]) -> Optional[str]:
    content = message.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list) and all(
        isinstance(b, dict)
        and b.get("type") == "text"
        and isinstance(b.get("text"), str)
        for b in content
    ):
        return "".join(b["text"] for b in content)
    return None


def _tool_calls_by_id(messages: List[Dict[str, Any]]) -> Dict[str, str]:
    calls: Dict[str, str] = {}
    for message in messages:
        if message.get("role") != "assistant":
            continue
        for call in message.get("tool_calls") or []:
            function = call.get("function") or {}
            if isinstance(call.get("id"), str):
                calls[call["id"]] = function.get("arguments") or ""
    return calls


def _arg_summary(arguments: str) -> str:
    """``k=v`` pairs on one line, values clipped, for the stub."""
    try:
        parsed = json.loads(arguments) if arguments else {}
    except ValueError:
        parsed = None
    if not isinstance(parsed, dict):
        pairs = [arguments.replace("\n", " ")]
    else:
        pairs = []
        for key, value in parsed.items():
            text = (
                value
                if isinstance(value, str)
                else json.dumps(value, ensure_ascii=False)
            )
            if len(text) > _SUMMARY_VALUE_CHARS:
                text = text[: _SUMMARY_VALUE_CHARS - 1] + "…"
            pairs.append(f"{key}={text.replace(chr(10), ' ')}")
    summary = ", ".join(pairs)
    if len(summary) > _SUMMARY_CHARS:
        summary = summary[: _SUMMARY_CHARS - 1] + "…"
    return summary


def _existing_handle(text: str, store) -> Optional[str]:
    """The archive a chunk-indexed result already points at, if it has one."""
    if '"artifact"' not in text:
        return None
    try:
        parsed = json.loads(text)
    except ValueError:
        return None
    if isinstance(parsed, list) and parsed and isinstance(parsed[-1], dict):
        parsed = parsed[-1]
    if not isinstance(parsed, dict):
        return None
    handle = parsed.get("artifact")
    return handle if isinstance(handle, str) and store.has(handle) else None


class ContextEvictor:
    """Per-agent eviction state: which results were sent when, and which are gone."""

    def __init__(self, threshold_tokens: int, keep_steps: int, min_batch_tokens: int):
        self.threshold_tokens = threshold_tokens
        self.keep_steps = keep_steps
        self.min_batch_tokens = min_batch_tokens
        #: The last measured prompt size, less what was evicted since.
        self.live_tokens = 0
        self.total_evicted = 0
        self._result_step: Dict[str, int] = {}
        self._evicted: set = set()

    def begin_turn(self) -> None:
        """Steps restart per turn; evictions carry over."""
        self.live_tokens = 0
        self._result_step.clear()

    def note_prompt_tokens(self, stats: Optional[Dict[str, Any]]) -> None:
        if not isinstance(stats, dict):
            return
        value = stats.get("prompt_tokens") or stats.get("input_tokens")
        if (
            isinstance(value, (int, float))
            and not isinstance(value, bool)
            and value > 0
        ):
            self.live_tokens = int(value)

    def evict(
        self, messages: List[Dict[str, Any]], step: int, store
    ) -> Optional[Dict[str, int]]:
        """Replace every evictable result in ``messages`` with its stub, or nothing.

        Called at the head of ``step`` before the model is asked. A result
        first seen now was produced in the previous step. Returns the batch's
        ``evicted_results`` and ``evicted_tokens_est`` when it fired.
        """
        candidates: List[Tuple[int, str, str]] = []
        for i, message in enumerate(messages):
            if message.get("role") != "tool":
                continue
            call_id = message.get("tool_call_id")
            key = call_id if isinstance(call_id, str) else f"#{i}"
            if key not in self._result_step:
                self._result_step[key] = step - 1
            if key in self._evicted or message.get("name") in NEVER_EVICTED_TOOLS:
                continue
            if step - self._result_step[key] <= self.keep_steps:
                continue
            text = _message_text(message)
            if text is not None:
                candidates.append((i, key, text))
        if self.live_tokens <= self.threshold_tokens or not candidates:
            return None
        batch_tokens = sum(estimate_tokens(text) for _, _, text in candidates)
        if batch_tokens < self.min_batch_tokens:
            return None

        arguments = _tool_calls_by_id(messages)
        for i, key, text in candidates:
            message = messages[i]
            name = message.get("name") or "tool"
            handle = _existing_handle(text, store)
            if handle is None:
                handle = store.put(text)
                store.set_index(handle, [{"offset": 0, "length": len(text)}])
                fetch = f"read_tool_output(artifact={handle}, entry=1)"
            else:
                # Its own index stays valid; the whole archive pages from 0.
                fetch = f"read_tool_output(artifact={handle}, offset=0, limit=8000)"
            summary = _arg_summary(arguments.get(key, ""))
            stub = (
                f"[evicted: {name} {summary}; {len(text)} chars; {fetch} fetches it]"
                if summary
                else f"[evicted: {name}; {len(text)} chars; {fetch} fetches it]"
            )
            messages[i] = {**message, "content": [{"type": "text", "text": stub}]}
            self._evicted.add(key)

        self.total_evicted += len(candidates)
        self.live_tokens = max(0, self.live_tokens - batch_tokens)
        logger.info(
            "Evicted %d tool results (~%d tokens) from the sent context at step %d; "
            "each stays readable via read_tool_output",
            len(candidates),
            batch_tokens,
            step,
        )
        return {
            "evicted_results": len(candidates),
            "evicted_tokens_est": batch_tokens,
        }
