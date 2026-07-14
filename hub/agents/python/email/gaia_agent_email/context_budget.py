# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""The email agent's context-window envelope (#1892).

``CONTEXT_TARGET_TOKENS`` (16384) is the ctx size the eval harness measures
and gates against: it comfortably fits the system prompt, tool schema, and a
full-thread triage prompt on the KV-cache budget of the consumer NPU/GPU
hardware GAIA targets. ``CONTEXT_MAX_TOKENS`` (32768) is the acceptable
ceiling for a deliberately larger run (e.g. a long-thread stress sweep) —
above it, KV-cache memory pressure on that hardware makes the measurement
unrepresentative of a real deployment. The eval path historically ran at
64K (Gemma-4-E4B's ``min_ctx_size`` in the Lemonade ``MODELS`` registry),
which is unrealistic for the machines this agent actually ships to; #1892
pins the 16K/32K envelope so every future ctx-affecting change (#1889's
long-thread budget, #1318's body-limit derivation, and the eval harness
itself) is measured against a number a real device can sustain, not an
optimistic default.
"""

from __future__ import annotations

CONTEXT_TARGET_TOKENS = 16384
CONTEXT_MAX_TOKENS = 32768

# Reserved off CONTEXT_TARGET_TOKENS for #1889's long-thread budget gate.
# Sized against the largest system prompt on the thread-triage path
# (llm_triage.py's classify prompt, ~1,009 estimated tokens) plus headroom for
# the tool schema on the agent-loop path.
_SYSTEM_PROMPT_ALLOWANCE_TOKENS = 1536

# Reserved for the LLM's own completion (classification JSON + summary text,
# or the fold digest) so the response isn't squeezed against the ctx ceiling.
_RESPONSE_RESERVE_TOKENS = 1024


def thread_budget_tokens() -> int:
    """Usable prompt-token budget for a thread transcript (#1889).

    ``CONTEXT_TARGET_TOKENS`` minus the system-prompt allowance and the
    response reserve — the slice actually available for message bodies once
    the surrounding prompt scaffolding and the model's own output are
    accounted for.
    """
    return (
        CONTEXT_TARGET_TOKENS
        - _SYSTEM_PROMPT_ALLOWANCE_TOKENS
        - _RESPONSE_RESERVE_TOKENS
    )


def estimate_tokens(text: str) -> int:
    """Conservative dual token estimate for ``text``.

    ``max(chars // 4, words * 1.3)`` — plain chars//4 under-counts
    non-ASCII/dense content (e.g. code, CJK text), so the word-based estimate
    acts as a floor.
    """
    if not text:
        return 0
    chars_estimate = len(text) // 4
    words_estimate = int(len(text.split()) * 1.3)
    return max(chars_estimate, words_estimate)
