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
