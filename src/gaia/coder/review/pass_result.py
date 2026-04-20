# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Common return type for every self-review pass.

Every pass (1-7) returns a :class:`PassResult`. The gate orchestrator
(:mod:`gaia.coder.review.gate`) reads these to decide pass/fail and
aggregates them into a :class:`GateResult` attached to the PR description.

The shape is a ``TypedDict`` rather than a dataclass so passes can be
generated from LLM JSON responses with minimal adaptation (:meth:`dict` is
the native envelope), and so it serialises cleanly to the audit log and to
the PR body without custom encoders.

Per §8 of ``docs/plans/coder-agent.mdx``, the fields mean:

* ``status``          — ``"pass"`` / ``"fail"`` / ``"skipped"``.
* ``findings``        — free-form list of dicts produced by the pass. Each
  deterministic pass documents its own finding shape in the pass module's
  docstring; LLM-driven passes echo the Pydantic-parsed JSON directly.
* ``confidence``      — 0–100 score where meaningful (Pass 2 = coverage
  %, Pass 6 = Opus adversarial confidence). ``None`` for deterministic
  passes that do not produce a score.
* ``citations``       — docs/spec references the pass consulted. Surfaced
  in the PR body so reviewers can trace a finding back to the rule.
* ``tooling_used``    — the actual binaries / APIs the pass invoked. Used
  by :mod:`gaia.coder.review.gate` to render the "how was this checked"
  section.
"""

from __future__ import annotations

from typing import List, Literal, Optional, TypedDict

PassStatus = Literal["pass", "fail", "skipped"]


class Finding(TypedDict, total=False):
    """One structured finding emitted by a pass.

    Passes are free to include additional fields; only ``severity`` and
    ``description`` are read by the gate orchestrator today. ``file`` and
    ``line`` are conventional and should be populated whenever applicable.
    """

    severity: Literal["blocking", "significant", "minor", "info"]
    description: str
    file: str
    line: int
    fix: str
    citation: str


class PassResult(TypedDict):
    """Envelope returned by every self-review pass (Passes 1–7)."""

    status: PassStatus
    findings: List[dict]
    confidence: Optional[int]
    citations: List[str]
    tooling_used: List[str]


def make_pass_result(
    status: PassStatus,
    findings: Optional[List[dict]] = None,
    confidence: Optional[int] = None,
    citations: Optional[List[str]] = None,
    tooling_used: Optional[List[str]] = None,
) -> PassResult:
    """Construct a :class:`PassResult` with sensible empty defaults.

    Prefer this constructor over a bare dict literal — it protects the
    callers from typos in field names (at least at call sites that pass
    through this helper) and centralises the "empty list not None"
    convention so the gate never needs to None-guard ``findings``.
    """
    return {
        "status": status,
        "findings": list(findings or []),
        "confidence": confidence,
        "citations": list(citations or []),
        "tooling_used": list(tooling_used or []),
    }


__all__ = ["Finding", "PassResult", "PassStatus", "make_pass_result"]
