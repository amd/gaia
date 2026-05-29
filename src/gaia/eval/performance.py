# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Performance metric extraction for GAIA eval benchmarks.

Harvests per-step latency/throughput/token metrics from the agent's
``conversation`` trace. The base ``Agent`` appends a system message of the
shape ``{"role": "system", "content": {"type": "stats",
"performance_stats": {...}}}`` after each LLM call
(see ``gaia.agents.base.agent``). ``performance_stats`` is whatever Lemonade's
``/stats`` endpoint returns; confirmed live keys (Gemma-4):

    input_tokens, output_tokens, prompt_tokens,
    time_to_first_token (seconds), tokens_per_second, decode_token_times

``total_tokens`` and per-step ``duration`` are NOT in ``/stats`` — they are
tolerated (token total falls back to input+output; run-level wall-clock
duration is supplied by the caller). This module is **domain-free**: it knows
nothing about email triage. Per-email classification (categories, confidence)
is extracted by the domain caller (e.g. ``gaia.eval.benchmark``) and passed in
via ``category_counts`` / ``total_emails``.

Sibling module ``gaia.perf_analysis`` covers the same TTFT/TPS vocabulary via
llama.cpp *log scraping*; this module is the *structured-stats* path.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any


@dataclass
class StepResult:
    """A single LLM call in the agent loop with its token/latency cost."""

    step_number: int
    action: str  # "llm_call", "planning", "final_answer"
    tool_name: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    reasoning_tokens: int = 0  # tokens in <thinking> blocks (estimated)
    total_tokens: int = 0
    duration_ms: int = 0
    time_to_first_token_ms: float = 0.0  # TTFT: prompt-send → first token
    tokens_per_second: float = 0.0  # TPS: inference throughput
    status: str = "ok"


@dataclass
class RunResult:
    """Run-level performance aggregate (domain-free).

    ``category_counts`` / ``total_emails`` are generic and supplied by the
    domain caller; this module never inspects classification output.
    """

    run_id: str
    timestamp: str
    model: str
    mode: str = ""
    step_results: list[StepResult] = field(default_factory=list)
    total_emails: int = 0
    total_duration_ms: int = 0
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    total_reasoning_tokens: int = 0
    total_tokens: int = 0
    avg_time_to_first_token_ms: float = 0.0
    avg_tokens_per_second: float = 0.0
    category_counts: dict[str, int] = field(default_factory=dict)
    is_cold_start: bool = False
    status: str = "ok"
    error: str = ""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _extract_reasoning_tokens(text: str) -> int:
    """Estimate reasoning tokens from ``<thinking>`` blocks in assistant text.

    Lemonade's /stats endpoint does not report reasoning tokens separately, so
    we approximate by counting characters inside ``<thinking>...</thinking>``
    blocks at a 1-token ≈ 4-character (BPE) ratio. Returns 0 if no blocks found.
    """
    thinking_blocks = re.findall(r"<thinking>(.*?)</thinking>", text, re.DOTALL)
    if not thinking_blocks:
        return 0
    total_chars = sum(len(b.strip()) for b in thinking_blocks)
    return max(1, total_chars // 4)


def _last_assistant_text(conversation: list, stats_msg: dict) -> str:
    """Return the last assistant message text before a system stats message."""
    try:
        idx = conversation.index(stats_msg)
    except ValueError:
        return ""
    for i in range(idx - 1, -1, -1):
        msg = conversation[i]
        if msg.get("role") == "assistant":
            text = msg.get("content", "")
            if isinstance(text, str):
                return text
            if isinstance(text, list):
                return "".join(b.get("text", "") for b in text if isinstance(b, dict))
    return ""


def extract_step_stats(conversation: list) -> tuple[list[StepResult], int]:
    """Extract per-step ``StepResult`` objects and total reasoning tokens.

    Walks the conversation for ``{"type": "stats"}`` system messages emitted by
    the base agent after each LLM call. Returns
    ``(step_results, total_reasoning_tokens)``.
    """
    step_results: list[StepResult] = []
    step_num = 0
    total_reasoning_tokens = 0
    last_tool_name = ""

    for msg in conversation:
        role = msg.get("role", "")

        # Track the tool name from the most recent tool result.
        if role == "tool" and msg.get("name"):
            last_tool_name = msg["name"]

        # A new assistant turn = new LLM call with no tool attributed yet.
        if role == "assistant":
            last_tool_name = ""
            assistant_text = msg.get("content", "")
            if isinstance(assistant_text, str) and assistant_text:
                reasoning = _extract_reasoning_tokens(assistant_text)
                if reasoning > 0:
                    total_reasoning_tokens += reasoning

        # Per-step stats live in system entries with a dict content.
        if role == "system" and isinstance(msg.get("content"), dict):
            content = msg["content"]
            if content.get("type") == "stats" and "performance_stats" in content:
                stats = content["performance_stats"]
                step_num += 1
                raw_ttft = stats.get("time_to_first_token")
                ttft_ms = float(raw_ttft) * 1000 if raw_ttft else 0.0
                in_tok = stats.get("input_tokens", 0) or 0
                out_tok = stats.get("output_tokens", 0) or 0
                step_results.append(
                    StepResult(
                        step_number=step_num,
                        action="llm_call",
                        tool_name=last_tool_name,
                        input_tokens=in_tok,
                        output_tokens=out_tok,
                        reasoning_tokens=_extract_reasoning_tokens(
                            _last_assistant_text(conversation, msg)
                        ),
                        total_tokens=(
                            stats.get("total_tokens", 0) or (in_tok + out_tok)
                        ),
                        # /stats has no per-step "duration"; tolerated as 0.
                        duration_ms=int((stats.get("duration", 0) or 0) * 1000),
                        time_to_first_token_ms=ttft_ms,
                        tokens_per_second=float(stats.get("tokens_per_second", 0) or 0),
                    )
                )

    return step_results, total_reasoning_tokens


# ---------------------------------------------------------------------------
# Main extraction
# ---------------------------------------------------------------------------


def extract_from_agent_result(
    agent_result: dict[str, Any],
    *,
    run_id: str,
    timestamp: str,
    model_id: str,
    mode: str = "full",
    total_duration_ms: int = 0,
    category_counts: dict[str, int] | None = None,
    total_emails: int = 0,
    is_cold_start: bool = False,
) -> RunResult:
    """Build a perf ``RunResult`` from a ``process_query()`` result dict.

    Domain metrics (``category_counts`` / ``total_emails``) are supplied by the
    caller — this function only harvests latency/throughput/token stats.

    Args:
        agent_result: dict returned by ``agent.process_query()`` (must carry a
            ``conversation`` list; may carry top-level ``input_tokens`` etc.).
        run_id / timestamp / model_id / mode: run identity.
        total_duration_ms: wall-clock duration measured by the caller.
        category_counts / total_emails: domain classification rollup (optional).
        is_cold_start: whether this was the first run of the model.
    """
    conversation = agent_result.get("conversation", [])
    step_results, total_reasoning_tokens = extract_step_stats(conversation)

    # Prefer top-level aggregates if present, else sum from steps.
    input_tokens = agent_result.get("input_tokens") or sum(
        s.input_tokens for s in step_results
    )
    output_tokens = agent_result.get("output_tokens") or sum(
        s.output_tokens for s in step_results
    )
    total_tokens = agent_result.get("total_tokens") or (input_tokens + output_tokens)

    ttft_vals = [
        s.time_to_first_token_ms for s in step_results if s.time_to_first_token_ms > 0
    ]
    tps_vals = [s.tokens_per_second for s in step_results if s.tokens_per_second > 0]
    avg_ttft = sum(ttft_vals) / len(ttft_vals) if ttft_vals else 0.0
    avg_tps = sum(tps_vals) / len(tps_vals) if tps_vals else 0.0

    return RunResult(
        run_id=run_id,
        timestamp=timestamp,
        model=model_id,
        mode=mode,
        step_results=step_results,
        total_emails=total_emails,
        total_duration_ms=total_duration_ms,
        total_input_tokens=input_tokens,
        total_output_tokens=output_tokens,
        total_reasoning_tokens=total_reasoning_tokens,
        total_tokens=total_tokens,
        avg_time_to_first_token_ms=round(avg_ttft, 1),
        avg_tokens_per_second=round(avg_tps, 1),
        category_counts=dict(category_counts or {}),
        is_cold_start=is_cold_start,
        status="ok",
    )


def to_performance_summary(run: RunResult) -> dict[str, Any]:
    """Render a ``RunResult`` into the per-scenario ``performance_summary`` dict
    that ``gaia.eval.scorecard.build_scorecard`` aggregates.

    Throughput/TTFT come straight from the harvested steps; pipeline latency is
    the caller-measured wall clock. ``flags`` carries gating-vs-reported notes.
    """
    return {
        "avg_tokens_per_second": run.avg_tokens_per_second,
        "avg_time_to_first_token": round(run.avg_time_to_first_token_ms / 1000.0, 4),
        "avg_time_to_first_token_ms": run.avg_time_to_first_token_ms,
        "total_input_tokens": run.total_input_tokens,
        "total_output_tokens": run.total_output_tokens,
        "total_tokens": run.total_tokens,
        "total_duration_ms": run.total_duration_ms,
        "total_emails": run.total_emails,
        "steps": len(run.step_results),
        "flags": [],
    }


def run_to_dict(run: RunResult) -> dict[str, Any]:
    """Serialize a ``RunResult`` (with its steps) to a JSON-serializable dict."""
    return {
        "run_id": run.run_id,
        "timestamp": run.timestamp,
        "model": run.model,
        "mode": run.mode,
        "total_emails": run.total_emails,
        "total_duration_ms": run.total_duration_ms,
        "total_input_tokens": run.total_input_tokens,
        "total_output_tokens": run.total_output_tokens,
        "total_reasoning_tokens": run.total_reasoning_tokens,
        "total_tokens": run.total_tokens,
        "avg_time_to_first_token_ms": run.avg_time_to_first_token_ms,
        "avg_tokens_per_second": run.avg_tokens_per_second,
        "category_counts": run.category_counts,
        "is_cold_start": run.is_cold_start,
        "status": run.status,
        "error": run.error,
        "step_results": [
            {
                "step_number": s.step_number,
                "action": s.action,
                "tool_name": s.tool_name,
                "input_tokens": s.input_tokens,
                "output_tokens": s.output_tokens,
                "reasoning_tokens": s.reasoning_tokens,
                "total_tokens": s.total_tokens,
                "duration_ms": s.duration_ms,
                "time_to_first_token_ms": s.time_to_first_token_ms,
                "tokens_per_second": s.tokens_per_second,
                "status": s.status,
            }
            for s in run.step_results
        ],
    }


def extract_from_trace_json(
    trace_path: str,
    *,
    run_id: str,
    timestamp: str,
    model_id: str,
    mode: str = "full",
    total_duration_ms: int = 0,
) -> RunResult:
    """Load a ``--trace`` JSON file and extract a perf ``RunResult``.

    Convenience wrapper for post-hoc perf analysis of any saved agent run.
    Raises ``FileNotFoundError`` / ``json.JSONDecodeError`` loudly on bad input.
    """
    with open(trace_path, "r", encoding="utf-8") as f:
        agent_result = json.load(f)
    return extract_from_agent_result(
        agent_result,
        run_id=run_id,
        timestamp=timestamp,
        model_id=model_id,
        mode=mode,
        total_duration_ms=total_duration_ms,
    )
