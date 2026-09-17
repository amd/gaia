# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Derive a model-routing policy from what the arms actually did.

A single default model is the wrong answer in both directions at once: it is
too expensive for the work that a small model handles perfectly, and not strong
enough for the work that defeats one. Routing fixes that — but only if the
assignment comes from measurement rather than intuition, because intuition
systematically over-estimates how much work is hard.

The rule is deliberately plain: **for each use case, take the cheapest model
that got it right every time it tried.** Where nothing clears that bar, the use
case escalates to the strongest model and is reported as unresolved rather than
quietly assigned.

Three things keep the recommendation honest:

* **Evidence is stated, never implied.** Most use cases have one or two
  attempts here. A model that passed once is marked ``low`` confidence, and the
  policy says so in the same row as the recommendation.
* **Irreversible work does not get routed down.** ``Gated`` use cases publish
  releases or rewrite history. Saving tokens there is a bad trade, so they pin
  to the strongest model regardless of what the cheap one managed.
* **Savings are weighted by real volume.** A use case that is 22% of recorded
  demand matters more than one that is 2%, and an unweighted count would make a
  policy look better than it is.
"""

from __future__ import annotations

from typing import Dict, List, Optional

from .enterprise import autonomy_table, family_of

#: Routable models, cheapest first. Ordering is by serving cost, not ability:
#: the whole point is to find the cheapest adequate option, so the search walks
#: this list and stops at the first model the evidence supports.
#:
#: Claude Code arms are deliberately absent. They are the reference harness we
#: measure against, not something GAIA can route a request to.
COST_ORDER = (
    "Qwen3.6-35B-A3B",
    "GPT-oss-120B",
    "Kimi-K2.7-Code",
    "claude-sonnet-5",
    "claude-opus-5",
)

#: Attempts below this make a "passed everything" record too weak to act on.
#: The recommendation is still made — withholding it helps nobody — but it is
#: labelled so a reader knows which rows are evidence and which are a guess.
CONFIDENT_ATTEMPTS = 3


def _cost_rank(model: str) -> int:
    return COST_ORDER.index(model) if model in COST_ORDER else len(COST_ORDER)


def routable_arms(metas: Dict[str, dict]) -> Dict[str, str]:
    """arm -> model, for arms a request could actually be routed to."""
    out = {}
    for arm, meta in metas.items():
        if (meta or {}).get("harness") == "claude-code":
            continue
        model = (meta or {}).get("model")
        if model:
            out[arm] = model
    return out


def policy(episodes, metas, reference: Optional[dict] = None) -> List[dict]:
    """One recommendation per use case, cheapest-adequate-first."""
    arms = routable_arms(metas)
    if not arms:
        return []
    strongest = max(arms.items(), key=lambda kv: _cost_rank(kv[1]))[1]
    places = {r["use_case"]: r for r in autonomy_table(episodes, metas)}
    volumes = (reference or {}).get("use_case_counts") or {}
    total_volume = sum(volumes.values()) or 1

    use_cases = sorted({e["use_case"] for eps in episodes.values() for e in eps})
    rows: List[dict] = []
    for uc in use_cases:
        scored = []
        for arm, model in arms.items():
            mine = [e for e in episodes[arm] if e["use_case"] == uc]
            if not mine:
                continue
            passed = sum(e["accomplished"] for e in mine)
            scored.append(
                {
                    "model": model,
                    "passed": passed,
                    "attempts": len(mine),
                    "clean": passed == len(mine),
                    "seconds": sum(e["wall_clock_s"] for e in mine) / len(mine),
                    "steps": sum(e["steps"] for e in mine) / len(mine),
                }
            )
        if not scored:
            continue
        scored.sort(key=lambda r: _cost_rank(r["model"]))

        gated = places.get(uc, {}).get("verdict_key") == "gated"
        cheapest = next((r for r in scored if r["clean"]), None)
        attempts = max(r["attempts"] for r in scored)

        if gated:
            pick, why = strongest, "irreversible — pinned to the strongest model"
            confidence = "policy"
        elif cheapest is None:
            pick, why = strongest, "no model passed cleanly — escalated, unresolved"
            confidence = "none"
        else:
            pick = cheapest["model"]
            saved = _cost_rank(strongest) - _cost_rank(pick)
            why = (
                f"cheapest clean pass ({cheapest['passed']}/{cheapest['attempts']})"
                if saved
                else "only the strongest model passed"
            )
            confidence = "good" if attempts >= CONFIDENT_ATTEMPTS else "low"

        rows.append(
            {
                "use_case": uc,
                "family": family_of(uc),
                "recommend": pick,
                "why": why,
                "confidence": confidence,
                "attempts": attempts,
                "downgraded": _cost_rank(pick) < _cost_rank(strongest),
                "share_of_work": round(100 * volumes.get(uc, 0) / total_volume, 1),
                "candidates": scored,
            }
        )
    return sorted(rows, key=lambda r: -r["share_of_work"])


def rollup(rows: List[dict]) -> List[dict]:
    """Share of real work each recommended model would carry."""
    by_model: Dict[str, Dict[str, float]] = {}
    for r in rows:
        slot = by_model.setdefault(
            r["recommend"], {"use_cases": 0, "share": 0.0, "low_confidence": 0}
        )
        slot["use_cases"] += 1
        slot["share"] += r["share_of_work"]
        if r["confidence"] in ("low", "none"):
            slot["low_confidence"] += 1
    return sorted(
        (
            {
                "model": model,
                "use_cases": v["use_cases"],
                "share": round(v["share"], 1),
                "low_confidence": v["low_confidence"],
            }
            for model, v in by_model.items()
        ),
        key=lambda r: _cost_rank(r["model"]),
    )
