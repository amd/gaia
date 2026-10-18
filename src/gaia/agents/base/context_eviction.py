# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Put already-consumed tool results away before they overflow the window.

Not compaction: nothing is summarised and no conversation turn is touched.
Only ``role: tool`` results the model has already read leave the context
*sent* to the model. Each one is archived whole in the agent's
:class:`~gaia.agents.base.artifacts.ArtifactStore` and replaced, in the sent
list only, by a one-line stub naming the tool, its key arguments, its outcome
and the ``read_tool_output`` call that re-reads it. The turn's conversation
log keeps every result whole.

When it fires: before each model call the prompt is measured (the backend's
own ``prompt_tokens`` for the previous request, plus a count of what changed
since). It fires on headroom, not a percentage (docs/plans/context-efficiency.md
§2): when the next call plus one more capped tool result (``headroom_tokens``)
would not fit in the usable window (the window less the reply's reserve and a
margin). Then the oldest eligible results go, in one batch, until there is room
for two. Every eviction changes the prompt prefix, so the backend re-reads
everything after the first stub once, uncached; the extra result's worth of
room keeps those events rare, and oldest-first keeps the break as late in the
prompt as it can be.

What never goes: the system prompt, user and assistant turns, the results of
the latest tool step, results seen by fewer than ``keep_steps`` model calls,
refusals (a denied permission or a governance block), ``delegate_task``
answers, and anything too small to be worth a stub.
"""

from __future__ import annotations

import json
import os
import re
from typing import Any, Dict, List, Optional, Tuple

from gaia.agents.base.tool_outcome import tool_result_dict
from gaia.agents.base.turn_metrics import count_tokens
from gaia.logger import get_logger

logger = get_logger(__name__)

CONTEXT_EVICTION_ENV_VAR = "GAIA_CONTEXT_EVICTION"
EVICT_THRESHOLD_ENV_VAR = "GAIA_EVICT_THRESHOLD"
EVICT_HEADROOM_ENV_VAR = "GAIA_EVICT_HEADROOM"
EVICT_KEEP_ENV_VAR = "GAIA_EVICT_KEEP"
EVICT_MIN_BATCH_ENV_VAR = "GAIA_EVICT_MIN_BATCH"

CONTEXT_EVICTION_MODES = ("off", "on", "auto")
#: Opt-in until the planted-fact retrieval gate passes (plan #5263).
DEFAULT_CONTEXT_EVICTION = "off"
#: One capped tool result (the plan's F, ~20,000 chars); Phase 0 traces refine it.
DEFAULT_EVICT_HEADROOM_TOKENS = 5000
#: Kept free beyond the reply's reserve, against tokenizer drift.
SAFETY_MARGIN_TOKENS = 2048
DEFAULT_EVICT_KEEP_STEPS = 2
#: The default smallest batch, as a share of the window: less is not worth a cache break.
DEFAULT_MIN_BATCH_RATIO = 0.05
#: Above this share of the window (or past the headroom) a small batch goes anyway.
URGENT_RATIO = 0.85
#: A stub costs about this many tokens.
_STUB_TOKENS = 40
#: A result that would free less than this stays.
MIN_EVICTABLE_TOKENS = 200

#: The parent's only view of a worker's run.
NEVER_EVICTED_TOOLS = frozenset({"delegate_task"})

#: A latest result over this many chars keeps only this much on an overflow retry.
OVERFLOW_PREVIEW_CHARS = 2000

_SUMMARY_VALUE_CHARS = 40
_SUMMARY_CHARS = 100
_OUTCOME_CHARS = 100

#: Marks a message as already evicted, judged from its own text: history keeps
#: the pre-eviction copy, so a result replayed on a later turn comes back
#: full-size under its original tool_call_id and must be judged fresh.
_EVICTED_STUB_PREFIX = "[Not shown, put away to save context: "
_SHORTENED_PREFIX = "[shortened to fit the context window: "

#: Real tokenizers count well above this share of our (deliberately high)
#: estimate; less is not a whole prompt, e.g. uncached tokens only.
_MIN_PLAUSIBLE_RATIO = 0.25

#: Seen-count for a result already in the request at a turn's first call: it
#: was read in an earlier turn.
_FROM_HISTORY = 10**9


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
    """``GAIA_EVICT_THRESHOLD`` (absolute tokens) parsed, or ``None``; malformed fails loudly."""
    return _positive_int_from_env(EVICT_THRESHOLD_ENV_VAR)


def evict_headroom_from_env() -> Optional[int]:
    """``GAIA_EVICT_HEADROOM`` parsed, or ``None``; malformed fails loudly."""
    return _positive_int_from_env(EVICT_HEADROOM_ENV_VAR)


def evict_keep_from_env() -> Optional[int]:
    """``GAIA_EVICT_KEEP`` parsed, or ``None``; malformed fails loudly."""
    return _positive_int_from_env(EVICT_KEEP_ENV_VAR)


def evict_min_batch_from_env() -> Optional[int]:
    """``GAIA_EVICT_MIN_BATCH`` parsed, or ``None``; malformed fails loudly."""
    return _positive_int_from_env(EVICT_MIN_BATCH_ENV_VAR)


def resolve_context_eviction(mode: str) -> bool:
    """Whether proactive eviction runs: ``on`` and ``auto`` do, ``off`` (the default) doesn't."""
    if mode not in CONTEXT_EVICTION_MODES:
        raise ValueError(
            f"context_eviction must be one of {', '.join(CONTEXT_EVICTION_MODES)}, "
            f"got {mode!r}"
        )
    return mode != "off"


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


def _arguments_by_position(messages: List[Dict[str, Any]]) -> Dict[int, str]:
    """Each tool result's call arguments, from the nearest call before it with its id."""
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


def _clip(text: str, limit: int) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _arg_summary(arguments: str) -> str:
    """``k=v`` pairs on one line, values clipped, for the stub."""
    try:
        parsed = json.loads(arguments) if arguments else {}
    except ValueError:
        parsed = None
    if not isinstance(parsed, dict):
        return _clip(arguments, _SUMMARY_CHARS)
    pairs = []
    for key, value in parsed.items():
        text = (
            value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
        )
        pairs.append(f"{key}={_clip(text, _SUMMARY_VALUE_CHARS)}")
    return _clip(", ".join(pairs), _SUMMARY_CHARS)


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


def _existing_handle(text: str, store) -> Optional[str]:
    """The archive a condensed result already points at, if it has one."""
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
        and isinstance(content, str)
    ):
        return None
    return handle, (
        f"read_tool_output(artifact={handle}, offset={offset}, "
        f"limit={max(1, len(content))})"
    )


def is_protected_result(message: Dict[str, Any]) -> bool:
    """A refusal or a worker's answer: the model must keep seeing it whole."""
    if message.get("name") in NEVER_EVICTED_TOOLS:
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


def _is_error_result(text: str) -> bool:
    parsed = tool_result_dict(text)
    return parsed is not None and parsed.get("status") == "error"


def is_stub(text: str) -> bool:
    return text.startswith(_EVICTED_STUB_PREFIX) or text.startswith(_SHORTENED_PREFIX)


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


def evicted_stub(
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
        f"{_EVICTED_STUB_PREFIX}{call}; summary: {_outcome_line(text)}; "
        f"{len(text)} chars. Call {fetch} to read it.]"
    )
    item = {
        "tool": name,
        "summary": summary,
        "artifact": handle,
        "chars": len(text),
        "tokens": message_tokens(message),
    }
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
    item = {
        "tool": name,
        "summary": summary,
        "artifact": handle,
        "chars": len(text),
        "tokens": message_tokens(message),
    }
    return (
        {
            **message,
            "content": [{"type": "text", "text": head + text[:OVERFLOW_PREVIEW_CHARS]}],
        },
        item,
    )


def message_tokens(message: Dict[str, Any]) -> int:
    """Leans high on purpose: cl100k undercounts JSON and CJK (#5066)."""
    text = json.dumps(message, ensure_ascii=False, default=str)
    return max(count_tokens(text), -(-len(text.encode("utf-8", "surrogatepass")) // 3))


def _k(tokens: int) -> str:
    return f"{tokens / 1000:.1f}K" if tokens >= 1000 else str(tokens)


def context_eviction_message(event: Dict[str, Any]) -> str:
    """The one line a user sees for a batch (wording from the context plan)."""
    n = event.get("evicted_results", 0)
    results = f"{n} older tool result{'s' if n != 1 else ''}"
    freed = _k(int(event.get("evicted_tokens_est") or 0))
    if event.get("reason") == "overflow":
        return (
            f"The request didn't fit the model's context window: moved {results} "
            f"out of the prompt (~{freed} tokens) and retried; still readable"
        )
    return f"Moved {results} out of the prompt (~{freed} tokens); still readable"


_HANDLE_RE = re.compile(r"artifact=(output_[0-9a-f]+)")

#: Asked once when the model answers over stubs it never re-read: a 4B model
#: otherwise answers from the stub's summary, or invents the rest.
REREAD_PROMPT = (
    "Some earlier tool results in this conversation are not shown (the lines that "
    'start "[Not shown, put away to save context:"). If your answer depends on any '
    "of them, call the read_tool_output that line names and read it before you "
    "answer; never answer from what you remember of it. If it doesn't, give the "
    "same answer again."
)


def answers_over_unread_stubs(messages: List[Dict[str, Any]], handles) -> bool:
    """A stub for one of *handles* is in the request and nothing was re-read after it."""

    def fresh_stub(m):
        text = (_message_text(m) or "") if m.get("role") == "tool" else ""
        match = _HANDLE_RE.search(text) if is_stub(text) else None
        return bool(match) and match.group(1) in handles

    first = next((i for i, m in enumerate(messages) if fresh_stub(m)), None)
    if first is None:
        return False
    return not any(
        m.get("role") == "tool" and m.get("name") == "read_tool_output"
        for m in messages[first:]
    )


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


class ContextEvictor:
    """Per-agent eviction state: the window, the measurement, which results were read."""

    def __init__(
        self,
        *,
        headroom_tokens: int = DEFAULT_EVICT_HEADROOM_TOKENS,
        keep_steps: int = DEFAULT_EVICT_KEEP_STEPS,
        threshold_tokens: Optional[int] = None,
        min_batch_tokens: Optional[int] = None,
    ):
        self.headroom_tokens = headroom_tokens
        self.keep_steps = keep_steps
        self.threshold_tokens = threshold_tokens
        self.min_batch_tokens = min_batch_tokens
        self.window_tokens: Optional[int] = None
        self.output_reserve = 0
        self.margin = SAFETY_MARGIN_TOKENS
        #: Stubs in the last request sent: handle -> tokens the result took.
        self.put_away: Dict[str, int] = {}
        #: Handles this turn moved out of its own results (not replayed history).
        self.fresh_handles: set = set()
        self._stub_tokens: Dict[str, int] = {}
        self._calls = 0
        self._seen: Dict[str, int] = {}
        self._first_call: Dict[str, int] = {}
        #: ``(backend prompt_tokens, our raw count)`` of the last request sent.
        self._anchor: Optional[Tuple[int, int]] = None
        self._last_raw: Optional[int] = None
        self._counts: Dict[int, Tuple[Any, Any, int, int]] = {}
        self._overhead: Optional[Tuple[Tuple[str, str], int]] = None

    # -- window and marks ------------------------------------------------

    def begin_turn(self, window_tokens: int, output_reserve: int = 0) -> None:
        if not isinstance(window_tokens, int) or window_tokens < 1:
            raise ValueError(
                f"context eviction needs the loaded window in tokens, got {window_tokens!r}"
            )
        self.fresh_handles = set()
        self.window_tokens = window_tokens
        # A small window can't spare an 8K reply reserve; a quarter is the most taken.
        self.output_reserve = min(output_reserve, window_tokens // 4)
        self.margin = min(SAFETY_MARGIN_TOKENS, window_tokens // 16)
        self._calls = 0
        self._seen.clear()
        self._first_call.clear()
        self._anchor = None
        self._last_raw = None
        self._counts.clear()

    def _window(self) -> int:
        if self.window_tokens is None:
            raise RuntimeError(
                "ContextEvictor.begin_turn() must run before the first call"
            )
        return self.window_tokens

    def usable(self) -> int:
        """Input tokens a request may hold: the window less reply reserve and margin."""
        return self._window() - self.output_reserve - self.margin

    def headroom(self) -> int:
        """One capped result, but never more than a quarter of the usable window."""
        return min(self.headroom_tokens, self.usable() // 4)

    def threshold(self) -> int:
        """Over this, the next call plus one more capped result would not fit."""
        if self.threshold_tokens is not None:
            return self.threshold_tokens
        return self.usable() - self.headroom()

    def target(self) -> int:
        """A batch goes on until there is room for two more capped results."""
        if self.threshold_tokens is not None:
            return self.threshold_tokens * 2 // 3
        return self.usable() - 2 * self.headroom()

    def min_batch(self) -> int:
        if self.min_batch_tokens is not None:
            return self.min_batch_tokens
        return int(self._window() * DEFAULT_MIN_BATCH_RATIO)

    # -- measurement -----------------------------------------------------

    def _count(self, message: Dict[str, Any]) -> int:
        content = message.get("content")
        size = len(content) if isinstance(content, (str, list)) else 0
        cached = self._counts.get(id(message))
        if (
            cached
            and cached[0] is message
            and cached[1] is content
            and cached[2] == size
        ):
            return cached[3]
        tokens = message_tokens(message)
        self._counts[id(message)] = (message, content, size, tokens)
        return tokens

    def overhead_tokens(self, system_prompt: str, tools: Any) -> int:
        """Tokenizer count of the system prompt and tool schemas."""
        key = (
            system_prompt or "",
            json.dumps(tools or [], ensure_ascii=False, default=str),
        )
        if self._overhead is None or self._overhead[0] != key:
            self._overhead = (key, count_tokens(key[0]) + count_tokens(key[1]))
        return self._overhead[1]

    def raw_tokens(self, messages, system_prompt: str, tools: Any) -> int:
        """Tokenizer count of everything the request carries."""
        return self.overhead_tokens(system_prompt, tools) + sum(
            self._count(m) for m in messages
        )

    def _backend_scale(self) -> float:
        """Backend tokens per tokenizer token, from the last plausible measurement."""
        if self._anchor is None:
            return 1.0
        measured, anchor_raw = self._anchor
        if not anchor_raw or measured < anchor_raw * _MIN_PLAUSIBLE_RATIO:
            return 1.0
        return measured / anchor_raw

    def resync(self, messages, system_prompt: str, tools: Any) -> None:
        """The request was rebuilt (an overflow retry): count what goes out now."""
        self._last_raw = self.raw_tokens(messages, system_prompt, tools)

    def estimate(self, messages, system_prompt: str, tools: Any) -> int:
        """The prompt's size: the backend's last measurement plus what changed since.

        The backend's count is the model's own tokenizer, so it wins over
        cl100k's (which overcounts Gemma by ~40%). A count under half the
        tokenizer's is not a whole prompt (e.g. uncached tokens only) and is
        not used.
        """
        raw = self.raw_tokens(messages, system_prompt, tools)
        if self._anchor is None:
            return raw
        measured, anchor_raw = self._anchor
        if measured < anchor_raw * _MIN_PLAUSIBLE_RATIO:
            return raw
        return max(0, measured + raw - anchor_raw)

    def note_prompt_tokens(self, stats: Optional[Dict[str, Any]]) -> None:
        """Anchor the estimate to what the backend measured for the last request."""
        if not isinstance(stats, dict) or self._last_raw is None:
            return
        value = stats.get("prompt_tokens") or stats.get("input_tokens")
        if (
            isinstance(value, (int, float))
            and not isinstance(value, bool)
            and value > 0
        ):
            self._anchor = (int(value), self._last_raw)

    # -- the policy ------------------------------------------------------

    def _keys(self, messages) -> List[Tuple[int, str]]:
        keys = []
        for i, message in enumerate(messages):
            if message.get("role") != "tool":
                continue
            # A backend may reuse ids per response; the position is unique.
            keys.append((i, f"{i}:{message.get('tool_call_id')}"))
        return keys

    def before_call(
        self, messages: List[Dict[str, Any]], store, *, system_prompt: str, tools: Any
    ) -> Optional[Dict[str, Any]]:
        """Measure the request about to go out and put old results away if it is too big.

        Mutates *messages* (the sent list) in place. Returns the batch's event
        when one fired, else ``None``.
        """
        self._calls += 1
        keys = self._keys(messages)
        for _, key in keys:
            if key not in self._seen:
                history = self._calls == 1
                self._seen[key] = _FROM_HISTORY if history else 0
                # 0 marks history, which never counts as the latest step.
                self._first_call[key] = 0 if history else self._calls
        latest = max(
            (self._first_call[k] for _, k in keys if self._first_call[k]),
            default=None,
        )

        event = self._maybe_evict(messages, keys, latest, store, system_prompt, tools)
        for _, key in keys:
            self._seen[key] += 1
        self._last_raw = self.raw_tokens(messages, system_prompt, tools)
        self.note_sent(messages)
        return event

    def note_sent(self, messages) -> None:
        """Record which results the request about to go out carries as stubs."""
        self.put_away = {}
        for message in messages:
            text = _message_text(message) if message.get("role") == "tool" else None
            if text is None or not is_stub(text):
                continue
            match = _HANDLE_RE.search(text)
            if match and match.group(1) in self._stub_tokens:
                self.put_away[match.group(1)] = self._stub_tokens[match.group(1)]

    def remember_stub(self, handle: str, tokens: int) -> None:
        self._stub_tokens[handle] = tokens

    def context_state(self) -> Dict[str, Any]:
        """``put_away``: results the last request carried as stubs, and their size."""
        if not self.put_away:
            return {"put_away": None, "summarized": None}
        return {
            "put_away": {
                "results": len(self.put_away),
                "tokens": sum(self.put_away.values()),
            },
            "summarized": None,
        }

    def _candidates(self, messages, keys, latest) -> List[Tuple[int, str]]:
        """Results the model has read and no longer needs whole, oldest first."""
        found = []
        for i, key in keys:
            message = messages[i]
            text = _message_text(message)
            if (
                text is None
                or is_stub(text)
                or is_protected_result(message)
                or _is_error_result(text)
                or self._first_call[key] == latest
                or self._seen[key] < self.keep_steps
            ):
                continue
            found.append((i, text))
        return found

    def _maybe_evict(self, messages, keys, latest, store, system_prompt, tools):
        before = self.estimate(messages, system_prompt, tools)
        # Counts below are our estimate; the marks are in the backend's own tokens.
        scale = self._backend_scale()
        threshold = self.threshold()
        if before <= threshold:
            return None
        candidates = self._candidates(messages, keys, latest)
        if not candidates:
            logger.debug(
                "Prompt ~%d tokens is over the eviction threshold %d of a %d-token "
                "window, but no tool result is old enough to put away",
                before,
                threshold,
                self._window(),
            )
            return None

        arguments = _arguments_by_position(messages)
        from_history = {i for i, key in keys if self._first_call[key] == 0}
        need = before - self.target()
        min_batch = self.min_batch()
        # Oldest first, until back under the target and the batch is worth it.
        planned: List[int] = []
        freed = 0
        for i, _ in candidates:
            if freed >= need and freed >= min_batch:
                break
            gain = self._count(messages[i]) - _STUB_TOKENS
            if gain < MIN_EVICTABLE_TOKENS:
                continue
            planned.append(i)
            freed += int(gain * scale)
        # Halfway into the headroom, a small batch goes anyway; before that it waits.
        urgent = before > min(
            self._window() * URGENT_RATIO, self.usable() - self.headroom() // 2
        )
        if not planned or (freed < min_batch and not urgent):
            return None

        items = []
        freed = 0
        for i in planned:
            message = messages[i]
            try:
                stub, item = evicted_stub(message, arguments.get(i, ""), store)
            except ValueError as e:
                # The archive is full: keep this result whole rather than lose it.
                logger.warning("Cannot put a tool result away: %s", e)
                break
            freed += self._count(message) - self._count(stub)
            self.remember_stub(item["artifact"], self._count(message))
            if i not in from_history:
                self.fresh_handles.add(item["artifact"])
            messages[i] = stub
            items.append(item)
        if not items:
            return None

        after = self.estimate(messages, system_prompt, tools)
        logger.info(
            "Put away %d tool results (~%d tokens): prompt ~%d -> ~%d of a %d-token "
            "window; each stays readable via read_tool_output",
            len(items),
            freed,
            before,
            after,
            self._window(),
        )
        return {
            "reason": "proactive",
            "evicted_results": len(items),
            "evicted_tokens_est": freed,
            "prompt_tokens_before": before,
            "prompt_tokens_after": after,
            "context_window": self._window(),
            "threshold_tokens": threshold,
            "items": items,
        }


def shrink_for_overflow(
    messages: List[Dict[str, Any]], store
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Every older result evicted, the latest step's large ones cut to a preview.

    The last-resort path after the backend refused a request as too long. It
    keeps refusals and already-stubbed results as they are, and every evicted
    byte stays readable. Returns ``(new messages, items put away)``.
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
        call_args = arguments.get(i, "")
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
            new, item = (shortened_result if latest else evicted_stub)(
                message, call_args, store
            )
        except ValueError as e:
            # The archive is full: keep this result whole rather than lose it.
            logger.warning("Cannot put a tool result away: %s", e)
            shrunk.append(message)
            continue
        shrunk.append(new)
        items.append(item)
    return shrunk, items
