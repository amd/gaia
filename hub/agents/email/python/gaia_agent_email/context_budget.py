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

# Fixed prompt cost of the agent loop's post-tool turn: the system prompt PLUS
# the full JSON tool schema the agent re-reads on every turn. Measured at ~8.4K
# on the bulk-triage path (#2087 CI run: 19,815-token request minus the ~11.4K
# verbatim 60-email envelope) — larger than the thread-triage path's
# ``_SYSTEM_PROMPT_ALLOWANCE_TOKENS`` because the agent loop carries every tool's
# schema, not just the classify prompt. The bulk-triage RESULT envelope
# re-read on the next turn must fit in what remains of CONTEXT_TARGET_TOKENS.
_AGENT_LOOP_FIXED_TOKENS = 9216


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


def envelope_budget_tokens() -> int:
    """Usable token budget for a tool-result envelope re-read on the agent
    loop's next turn (#2087).

    ``CONTEXT_TARGET_TOKENS`` minus the agent-loop fixed prompt cost (system
    prompt + full tool schema) and the response reserve — the slice actually
    available for a tool result once the surrounding scaffolding and the model's
    own output are accounted for. Bulk triage condenses its result envelope to
    fit this so the post-tool turn stays under ``CONTEXT_TARGET_TOKENS``.
    """
    return (
        CONTEXT_TARGET_TOKENS
        - _AGENT_LOOP_FIXED_TOKENS
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


def estimate_tokens_json(text: str) -> int:
    """Token estimate calibrated for compact serialized JSON (#2087).

    ``chars // 4`` is a prose ratio; real tokenizers are far denser on JSON,
    and the density depends on the content mix — both measured on hardware:

    - verbatim 60-email envelope (prose-heavy subjects/rationales):
      23,965 chars → ~11.4K tokens ≈ **2.1 chars/token** (the uncondensed
      post-tool turn 400'd at 19,815 vs a 16,384 window);
    - condensed 300-email envelope (dominated by the hex-id ``grouped`` map):
      still overflowed by 672 tokens under a chars//2 estimate ≈
      **~1.4 chars/token** — hex ids fragment into near-per-character tokens.

    Assume the densest plausible mix: **1.3 chars/token**, the practical floor
    for ASCII JSON. Over-counting prose-heavy envelopes wastes only exemplar
    slots (``grouped`` keeps the complete verdict map either way); an
    under-count is a 400 on real hardware. Always round pessimistically.
    """
    if not text:
        return 0
    return (len(text) * 10 + 12) // 13
