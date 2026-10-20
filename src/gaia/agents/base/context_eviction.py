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
then all of them go in one batch. Eligibility is decided from each message's
own text (already a stub, or not), not a remembered id: a result replayed at
full size from conversation history on a later turn -- the turn log keeps the
pre-eviction copy, so history always replays results whole -- is eligible
again once it re-ages past ``keep_steps``.
"""

from __future__ import annotations

import json
import math
import os
import re
from typing import Any, Dict, List, Optional, Tuple

from gaia.agents.base.tool_outcome import tool_result_dict
from gaia.agents.base.turn_metrics import count_tokens
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

#: A worker's answer is the parent's only view of it.
PROTECTED_TOOLS = frozenset({"delegate_task"})
#: Their results are bounded pages or the parent's only view of a worker.
NEVER_EVICTED_TOOLS = PROTECTED_TOOLS | {"read_tool_output"}

#: Characters of the latest step's large result an overflow retry still shows.
OVERFLOW_PREVIEW_CHARS = 2000

_SUMMARY_VALUE_CHARS = 40
_SUMMARY_CHARS = 100
_OUTCOME_CHARS = 100

#: Marks a message as already evicted. Checked against the message's own
#: text rather than a remembered id: history keeps the pre-eviction copy, so
#: a result replayed on a later turn comes back full-size under its original
#: tool_call_id and must be judged fresh, not skipped as "already done".
_EVICTED_STUB_PREFIX = "[evicted: "
#: What the overflow retry leaves in place of a result it moved out.
_PUT_AWAY_PREFIX = "[Not shown, put away to save context: "
_SHORTENED_PREFIX = "[shortened to fit the context window: "


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


def is_stub(text: str) -> bool:
    """Whether *text* is what eviction or the overflow retry left for a result."""
    return text.startswith((_EVICTED_STUB_PREFIX, _PUT_AWAY_PREFIX, _SHORTENED_PREFIX))


def _clip(text: str, limit: int) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _arguments_by_position(messages: List[Dict[str, Any]]) -> Dict[int, str]:
    """Each tool result's call arguments, from the nearest call before it with its id.

    Keyed by position: some backends reuse a ``tool_call_id`` across responses.
    """
    calls: Dict[str, str] = {}
    found: Dict[int, str] = {}
    for i, message in enumerate(messages):
        if message.get("role") == "assistant":
            for call in message.get("tool_calls") or []:
                function = call.get("function") or {}
                if isinstance(call.get("id"), str):
                    calls[call["id"]] = function.get("arguments") or ""
        elif message.get("role") == "tool":
            found[i] = calls.get(message.get("tool_call_id") or "", "")
    return found


def _outcome_line(text: str) -> str:
    """What the result said, in a few words: its status and message, or its first line."""
    parsed = tool_result_dict(text)
    if parsed is not None:
        parts = []
        status = parsed.get("status")
        if isinstance(status, str):
            parts.append(status)
        for key in ("error", "message", "summary", "answer"):
            value = parsed.get(key)
            if isinstance(value, str) and value.strip():
                parts.append(value)
                break
        for key, value in parsed.items():
            if isinstance(value, list) and value:
                parts.append(f"{len(value)} {key}")
                break
        if parts:
            return _clip(", ".join(parts), _OUTCOME_CHARS)
    stripped = text.lstrip()
    if stripped.startswith("["):
        try:
            items = json.loads(stripped)
        except ValueError:
            items = None
        if isinstance(items, list):
            return f"{len(items)} items"
    for line in text.splitlines():
        if line.strip():
            return _clip(line, _OUTCOME_CHARS)
    return "empty"


def _reread_page(text: str, store) -> Optional[Tuple[str, str]]:
    """``(handle, call)`` that reads a ``read_tool_output`` page again, from its own fields."""
    parsed = tool_result_dict(text)
    if parsed is None:
        return None
    handle, offset, content = (
        parsed.get("artifact"),
        parsed.get("offset"),
        parsed.get("content"),
    )
    if not (
        isinstance(handle, str)
        and store.has(handle)
        and isinstance(offset, int)
        and not isinstance(offset, bool)
        and isinstance(content, str)
    ):
        return None
    return handle, (
        f"read_tool_output(artifact={handle}, offset={offset}, "
        f"limit={max(1, len(content))})"
    )


def is_protected_result(message: Dict[str, Any]) -> bool:
    """A refusal or a worker's answer: the model must keep seeing it whole."""
    if message.get("name") in PROTECTED_TOOLS:
        return True
    text = _message_text(message)
    parsed = tool_result_dict(text) if text is not None else None
    if parsed is None:
        return False
    return (
        parsed.get("status") == "denied"
        or parsed.get("executed") is False
        or "governance_decision" in parsed
    )


def _archive(text: str, store) -> Tuple[str, str]:
    """``(handle, fetch call)`` for *text*, reusing an archive it already names."""
    reread = _reread_page(text, store)
    if reread is not None:
        store.touch(reread[0])
        return reread
    handle = _existing_handle(text, store)
    if handle is not None:
        store.touch(handle)
        # Its own index stays valid; the whole archive pages from 0.
        return handle, f"read_tool_output(artifact={handle}, offset=0, limit=8000)"
    handle = store.put_result(text)
    return handle, f"read_tool_output(artifact={handle}, entry=1)"


def message_tokens(message: Dict[str, Any]) -> int:
    """Leans high on purpose: cl100k undercounts JSON and CJK (#5066)."""
    text = json.dumps(message, ensure_ascii=False, default=str)
    return max(count_tokens(text), -(-len(text.encode("utf-8", "surrogatepass")) // 3))


def request_tokens(messages: List[Dict[str, Any]], system_prompt: str, tools) -> int:
    """Count of everything a request carries, leaning high."""
    overhead = count_tokens(system_prompt or "") + count_tokens(
        json.dumps(tools or [], ensure_ascii=False, default=str)
    )
    return overhead + sum(message_tokens(m) for m in messages)


def _put_away_item(message, name: str, summary: str, handle: str, text: str) -> dict:
    return {
        "tool": name,
        "summary": summary,
        "artifact": handle,
        "chars": len(text),
        "tokens": message_tokens(message),
    }


def put_away_stub(
    message: Dict[str, Any], arguments: str, store
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """``(stub message, item)`` for one result, archiving it whole first."""
    text = _message_text(message) or ""
    name = message.get("name") or "tool"
    handle, fetch = _archive(text, store)
    summary = _arg_summary(arguments)
    call = f"{name} {summary}" if summary else name
    # Say outright the text is absent: a 4B model otherwise answers from the summary.
    stub = (
        f"{_PUT_AWAY_PREFIX}{call}; summary: {_outcome_line(text)}; "
        f"{len(text)} chars. Call {fetch} to read it.]"
    )
    item = _put_away_item(message, name, summary, handle, text)
    return {**message, "content": [{"type": "text", "text": stub}]}, item


def shortened_result(
    message: Dict[str, Any], arguments: str, store
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """The latest result cut to a head preview, the rest one call away."""
    text = _message_text(message) or ""
    name = message.get("name") or "tool"
    handle = store.put_result(text)
    summary = _arg_summary(arguments)
    call = f"{name} {summary}" if summary else name
    head = (
        f"{_SHORTENED_PREFIX}{call}; showing the first {OVERFLOW_PREVIEW_CHARS} of "
        f"{len(text)} chars; read_tool_output(artifact={handle}, "
        f"offset={OVERFLOW_PREVIEW_CHARS}) continues]\n"
    )
    item = _put_away_item(message, name, summary, handle, text)
    preview = head + text[:OVERFLOW_PREVIEW_CHARS]
    return {**message, "content": [{"type": "text", "text": preview}]}, item


_OVERFLOW_NUMBERS = (
    re.compile(
        r"request \((\d+) tokens\) exceeds the available context size \((\d+) tokens\)"
    ),
    re.compile(r"n_prompt_tokens\W+(\d+).*?n_ctx\W+(\d+)", re.DOTALL),
)


def overflow_numbers(error_text: str) -> Optional[Tuple[int, int]]:
    """``(requested, available)`` tokens from a backend's overflow error, if it says."""
    for pattern in _OVERFLOW_NUMBERS:
        match = pattern.search(error_text or "")
        if match:
            return int(match.group(1)), int(match.group(2))
    return None


def shrink_for_overflow(
    messages: List[Dict[str, Any]], store
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Every older result put away, the latest step's large ones cut to a preview.

    The last-resort path after the backend refused a request as too long. It
    keeps refusals, worker answers and already-stubbed results as they are, and
    every byte it moves out stays readable through ``read_tool_output`` (#2780).
    Returns ``(new messages, items put away)``; *messages* is not mutated.
    """
    arguments = _arguments_by_position(messages)
    last_assistant = max(
        (i for i, m in enumerate(messages) if m.get("role") == "assistant"), default=-1
    )
    shrunk: List[Dict[str, Any]] = []
    items: List[Dict[str, Any]] = []
    for i, message in enumerate(messages):
        text = _message_text(message) if message.get("role") == "tool" else None
        if text is None or is_stub(text) or is_protected_result(message):
            shrunk.append(message)
            continue
        latest = i > last_assistant
        if latest and message.get("name") == "read_tool_output":
            # Already a bounded page; cutting it would undo the re-read just made.
            shrunk.append(message)
            continue
        if len(text) <= (
            OVERFLOW_PREVIEW_CHARS if latest else OVERFLOW_PREVIEW_CHARS // 4
        ):
            # Short enough to keep: a stub would save little or nothing.
            shrunk.append(message)
            continue
        try:
            new, item = (shortened_result if latest else put_away_stub)(
                message, arguments.get(i, ""), store
            )
        except ValueError as e:
            # The archive is full: keep this result whole rather than lose it.
            logger.warning("Cannot put a tool result away: %s", e)
            shrunk.append(message)
            continue
        shrunk.append(new)
        items.append(item)
    return shrunk, items


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

    def begin_turn(self) -> None:
        """Steps restart per turn; evictions carry over via each stub's own text.

        Clearing ``_result_step`` means a result replayed from history (still
        full text -- the turn log keeps the pre-eviction copy) gets a fresh
        ``keep_steps`` grace window rather than resuming its true age. That
        only delays re-eviction by a turn or two; it never prevents it, since
        eligibility is now decided from the message's own text.
        """
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
            text = _message_text(message)
            if (
                message.get("name") in NEVER_EVICTED_TOOLS
                or text is None
                or is_stub(text)
            ):
                continue
            if step - self._result_step[key] <= self.keep_steps:
                continue
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
                f"{_EVICTED_STUB_PREFIX}{name} {summary}; {len(text)} chars; "
                f"{fetch} fetches it]"
                if summary
                else f"{_EVICTED_STUB_PREFIX}{name}; {len(text)} chars; {fetch} fetches it]"
            )
            messages[i] = {**message, "content": [{"type": "text", "text": stub}]}

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
