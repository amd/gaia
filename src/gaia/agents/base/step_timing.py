# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Where an agent turn's wall time went, step by step.

Each step's ``stats`` record gets a breakdown measured on this process's own
clock: model time, tool time, and everything else (overhead). Overhead is the
remainder of the already-rounded parts, so the reported ``llm_seconds +
tool_seconds + overhead_seconds`` adds back to the reported ``step_seconds``
exactly. Token counts come from each call's own
response usage, never from Lemonade's ``/stats`` — that endpoint only measures
local generation and says nothing true about a cloud model.

A step's window runs from the top of its loop iteration to the top of the
next one (or the end of the loop), so every second of the loop belongs to
exactly one step. Time before the first step and after the last is run-level
overhead and shows up only in :meth:`StepTimer.finish`.
"""

import threading
import time
from typing import Any, Callable, Dict, List, Optional

from gaia.logger import get_logger

log = get_logger(__name__)

#: How many of the slowest steps the run summary names.
SLOWEST_STEPS = 3

_ROUND = 4


def _num(value: Any) -> Optional[float]:
    """A real non-negative number, or ``None`` for anything else (bools too)."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return value if value >= 0 else None


def _sum_known(values: List[Optional[float]]) -> Optional[float]:
    """Sum of the reported values; ``None`` when none were reported."""
    known = [v for v in values if v is not None]
    return sum(known) if known else None


def _output_tok_per_s(calls: List[Dict[str, Any]]) -> Optional[float]:
    """Completion tokens divided by generation seconds, over calls that
    reported their tokens.

    A streamed call counts the tokens after the first over the time after the
    first (``seconds - ttft_seconds``): pure decode. A non-streamed call has no
    first-token mark, so it counts all its tokens over its whole wall time —
    prefill and network included, which makes the rate a lower bound.
    """
    tokens = 0.0
    seconds = 0.0
    for call in calls:
        completion = call.get("completion_tokens")
        if completion is None:
            continue
        ttft = call.get("ttft_seconds")
        if ttft is None:
            gen_tokens, gen = completion, call["seconds"]
        else:
            gen_tokens, gen = completion - 1, call["seconds"] - ttft
        if gen <= 0 or gen_tokens <= 0:
            continue
        tokens += gen_tokens
        seconds += gen
    if tokens <= 0 or seconds <= 0:
        return None
    return round(tokens / seconds, 2)


class StepTimer:
    """Accumulates one turn's per-step timing. Construct once per turn.

    Only calls made on the thread that built the timer are counted. A tool
    body runs on a worker thread, so an LLM call it makes is already inside
    that tool's seconds and must not be counted as model time a second time.
    """

    def __init__(self, clock: Callable[[], float] = time.perf_counter) -> None:
        self._clock = clock
        self._owner = threading.get_ident()
        self._t0 = clock()
        self._current: Optional[Dict[str, Any]] = None
        self._steps: List[Dict[str, Any]] = []
        # Calls made outside any step (before the loop, after it) still count
        # toward the run totals.
        self._loose_llm_calls: List[Dict[str, Any]] = []
        self._loose_tool_seconds = 0.0
        self._summary: Optional[Dict[str, Any]] = None

    def _on_owner_thread(self, what: str) -> bool:
        if threading.get_ident() == self._owner:
            return True
        log.debug("step timer: ignoring %s from a non-loop thread", what)
        return False

    # ── step lifecycle ─────────────────────────────────────────────────────

    def begin_step(self, step: int) -> None:
        """Close the open step, if any, and open *step* now."""
        now = self._clock()
        self._close(now)
        self._current = {
            "step": step,
            "start": now,
            "llm_calls": [],
            "tool_calls": [],
            "record": None,
        }

    def attach(self, record: Dict[str, Any]) -> None:
        """Bind the open step's ``stats`` record; it is filled when the step
        closes, because the step keeps running after the record is written."""
        if self._current is None:
            raise RuntimeError(
                "StepTimer.attach() called with no open step; call "
                "begin_step() at the top of the loop iteration first."
            )
        self._current["record"] = record

    def has_llm_calls(self) -> bool:
        """Whether the open step has made at least one model call."""
        return bool(self._current and self._current["llm_calls"])

    # ── measurements ───────────────────────────────────────────────────────

    def record_llm_call(self, call: Dict[str, Any]) -> None:
        """One model request, as measured by the chat SDK.

        Expected keys: ``seconds`` (request wall time), ``ttft_seconds``,
        ``finish_reason``, ``completion_tokens``, ``reasoning_tokens``.
        """
        if not self._on_owner_thread("an LLM call"):
            return
        seconds = _num(call.get("seconds"))
        if seconds is None:
            raise ValueError(
                f"LLM call record has no valid 'seconds' (got {call!r}); the "
                "chat SDK must report the request's wall time."
            )
        entry = {
            "seconds": seconds,
            "ttft_seconds": _num(call.get("ttft_seconds")),
            "finish_reason": call.get("finish_reason") or None,
            "completion_tokens": _num(call.get("completion_tokens")),
            "reasoning_tokens": _num(call.get("reasoning_tokens")),
        }
        if self._current is None:
            self._loose_llm_calls.append(entry)
        else:
            self._current["llm_calls"].append(entry)

    def record_tool(self, name: str, seconds: float, waited: float = 0.0) -> None:
        """One tool execution. *seconds* is its full wall time, including any
        *waited* seconds spent blocked on a human approving it."""
        if not self._on_owner_thread("a tool call"):
            return
        if self._current is None:
            self._loose_tool_seconds += seconds
            return
        entry: Dict[str, Any] = {"name": name, "seconds": round(seconds, _ROUND)}
        if waited > 0:
            entry["approval_wait_seconds"] = round(waited, _ROUND)
        self._current["tool_calls"].append((entry, seconds))

    # ── results ────────────────────────────────────────────────────────────

    def _close(self, now: float) -> None:
        step = self._current
        if step is None:
            return
        self._current = None
        calls = step["llm_calls"]
        wall = now - step["start"]
        llm = sum((c["seconds"] for c in calls), 0.0)
        tools = sum((raw for _entry, raw in step["tool_calls"]), 0.0)
        first = calls[0] if calls else None
        last = calls[-1] if calls else None
        r_wall = round(wall, _ROUND)
        r_llm = round(llm, _ROUND)
        r_tools = round(tools, _ROUND)
        fields = {
            "step_seconds": r_wall,
            "llm_seconds": r_llm,
            "tool_seconds": r_tools,
            # Remainder of the *rounded* parts, so the three reported values
            # add back to the reported step exactly.
            "overhead_seconds": round(r_wall - r_llm - r_tools, _ROUND),
            # First call only: a later call's ttft is not what the step waited.
            "ttft_seconds": (
                round(first["ttft_seconds"], _ROUND)
                if first and first["ttft_seconds"] is not None
                else None
            ),
            # The last call's, since its output is what the step acted on.
            "finish_reason": last["finish_reason"] if last else None,
            "reasoning_tokens": _sum_known([c["reasoning_tokens"] for c in calls]),
            "output_tok_per_s": _output_tok_per_s(calls),
            # More than one means the step retried or continued a reply.
            "llm_calls": len(calls),
            "tools": [entry for entry, _raw in step["tool_calls"]],
        }
        self._steps.append(
            {
                **fields,
                "step": step["step"],
                "_wall": wall,
                "_llm": llm,
                "_tools": tools,
                "completion_tokens": _sum_known(
                    [c["completion_tokens"] for c in calls]
                ),
            }
        )
        if step["record"] is not None:
            step["record"].update(fields)

    def finish(self) -> Dict[str, Any]:
        """Close the open step and return the run summary. Idempotent."""
        if self._summary is not None:
            return self._summary
        now = self._clock()
        self._close(now)
        wall = now - self._t0
        llm = sum((s["_llm"] for s in self._steps), 0.0) + sum(
            c["seconds"] for c in self._loose_llm_calls
        )
        tools = sum(s["_tools"] for s in self._steps) + self._loose_tool_seconds
        r_wall = round(wall, _ROUND)
        r_llm = round(llm, _ROUND)
        r_tools = round(tools, _ROUND)
        all_calls = self._loose_llm_calls + [
            {"completion_tokens": s["completion_tokens"]} for s in self._steps
        ]
        slowest = sorted(self._steps, key=lambda s: s["_wall"], reverse=True)
        self._summary = {
            "wall_seconds": r_wall,
            "llm_seconds": r_llm,
            "tool_seconds": r_tools,
            "overhead_seconds": round(r_wall - r_llm - r_tools, _ROUND),
            "llm_calls": len(self._loose_llm_calls)
            + sum(s["llm_calls"] for s in self._steps),
            "output_tokens": _sum_known([c["completion_tokens"] for c in all_calls]),
            "slowest_steps": [self._slow_step(s) for s in slowest[:SLOWEST_STEPS]],
        }
        return self._summary

    @staticmethod
    def _slow_step(step: Dict[str, Any]) -> Dict[str, Any]:
        parts = {
            "llm": step["_llm"],
            "tools": step["_tools"],
            "overhead": step["_wall"] - step["_llm"] - step["_tools"],
        }
        return {
            "step": step["step"],
            "step_seconds": step["step_seconds"],
            # Which part of the step the time went to.
            "dominant": max(parts, key=parts.get),
            "llm_seconds": step["llm_seconds"],
            "tool_seconds": step["tool_seconds"],
            "overhead_seconds": step["overhead_seconds"],
            "output_tokens": step["completion_tokens"],
            "reasoning_tokens": step["reasoning_tokens"],
            "finish_reason": step["finish_reason"],
            "llm_calls": step["llm_calls"],
            "tools": [t["name"] for t in step["tools"]],
        }
