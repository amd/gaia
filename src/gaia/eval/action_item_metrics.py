# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Extraction-quality metrics for email action items (#1605 / #1949).

The email agent's triage surface extracts ``action_items`` from every email
(PR #1917) and persists them as durable tasks. This module measures whether it
pulls the RIGHT items: precision / recall / F1 of the extracted set against a
hand-labeled ground-truth corpus (``tests/fixtures/email/
action_items_ground_truth.json``), plus reported secondaries (due-hint
accuracy, link accuracy, hard-negative cleanliness).

Design mirrors :mod:`gaia.eval.quality_metrics` (the triage FP/FN gate):

* Scoring reuses :class:`~gaia.eval.quality_metrics.Confusion`, so the
  ``action_items`` block carries the same keys as the existing quality axes
  (``tp``/``fp``/``fn``/``precision``/``recall``/``f1``…).
* The gate bars + the single ``enforce`` switch live in a committed manifest
  (``tests/fixtures/email/action_items_gate_thresholds.json``), read only
  through :func:`load_action_item_thresholds`.
  :func:`evaluate_action_item_gate` returns the same shape as
  ``quality_metrics.evaluate_gate`` (``passed``/``breaches``/``enforce``/
  ``should_fail``) so CI consumes it identically. The manifest ships
  ``enforce: false`` — report mode.

**Matching is set-based and semantic-tolerant.** Expected and extracted items
are matched one-to-one on their descriptions: exact match on normalized text
first (lowercase, punctuation stripped, whitespace collapsed), then a fuzzy
pass (``difflib`` ratio >= :data:`FUZZY_MATCH_THRESHOLD`) to absorb minor
rewording. An optional LLM judge (see :func:`make_claude_equivalence_judge`)
can arbitrate pairs in the ambiguity band below the fuzzy bar — it is opt-in,
budget-capped, and never the primary signal; the default path is fully
deterministic and offline.

The extractor under test (``EmailTriageService._extract_action_items``) is
itself deterministic, so :func:`run_action_item_extraction` +
:func:`score_action_item_extraction` need no Lemonade server, no LLM, and no
network — the eval runs anywhere the email agent package is installed.
"""

from __future__ import annotations

import difflib
import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping, Optional, Sequence

from gaia.eval.quality_metrics import Confusion

logger = logging.getLogger(__name__)

# Keys in the ground-truth file that are metadata, not labeled cases.
_METADATA_KEYS = {"_meta", "_comment", "_metadata"}

# Similarity (difflib ratio over normalized text) at or above which two
# descriptions count as the same action item without a judge.
FUZZY_MATCH_THRESHOLD = 0.80

# Floor of the judge ambiguity band: pairs scoring in
# [JUDGE_BAND_FLOOR, FUZZY_MATCH_THRESHOLD) MAY be sent to the optional judge.
# Below the floor the pair is plainly different — burning a judge call on it
# would be waste.
JUDGE_BAND_FLOOR = 0.50

# Default cap on judge calls per scoring run (cost bound). Overrunning the cap
# is recorded in the output (judge.skipped_over_budget) and logged — never
# silent.
DEFAULT_MAX_JUDGE_CALLS = 25

_NON_ALNUM_RE = re.compile(r"[^a-z0-9]+")


def normalize_action_text(text: str) -> str:
    """Canonical form for description matching: lowercase, punctuation →
    space, whitespace collapsed."""
    return _NON_ALNUM_RE.sub(" ", (text or "").lower()).strip()


def text_similarity(a: str, b: str) -> float:
    """``difflib`` ratio over the normalized texts, in [0, 1]."""
    return difflib.SequenceMatcher(
        None, normalize_action_text(a), normalize_action_text(b)
    ).ratio()


@dataclass
class MatchResult:
    """One-to-one matching of expected vs extracted descriptions for a case."""

    # (expected_index, extracted_index, method, similarity)
    matched: list[tuple[int, int, str, float]] = field(default_factory=list)
    unmatched_expected: list[int] = field(default_factory=list)
    unmatched_extracted: list[int] = field(default_factory=list)
    judge_calls: int = 0
    judge_accepted: int = 0
    judge_skipped_over_budget: int = 0


def match_action_items(
    expected: Sequence[str],
    extracted: Sequence[str],
    *,
    fuzzy_threshold: float = FUZZY_MATCH_THRESHOLD,
    judge: Optional[Callable[[str, str], bool]] = None,
    judge_band_floor: float = JUDGE_BAND_FLOOR,
    judge_budget: Optional[list[int]] = None,
) -> MatchResult:
    """Greedily match expected to extracted descriptions, one-to-one.

    Pass priority: exact normalized match, then fuzzy (best similarity first,
    at or above ``fuzzy_threshold``), then — only when ``judge`` is provided —
    an equivalence call for still-unmatched pairs whose similarity falls in
    ``[judge_band_floor, fuzzy_threshold)``.

    ``judge_budget`` is a single-element mutable counter of remaining judge
    calls, shared across cases by the corpus scorer so the cost bound is
    global. When the budget is exhausted, remaining candidate pairs stay
    unmatched and are counted in ``judge_skipped_over_budget`` (reported and
    logged — a capped judge never silently changes semantics).
    """
    result = MatchResult()
    exp_open = list(range(len(expected)))
    ext_open = list(range(len(extracted)))

    # Pass 1 — exact normalized match.
    ext_by_norm: dict[str, list[int]] = {}
    for j in ext_open:
        ext_by_norm.setdefault(normalize_action_text(extracted[j]), []).append(j)
    for i in list(exp_open):
        candidates = ext_by_norm.get(normalize_action_text(expected[i]), [])
        if candidates:
            j = candidates.pop(0)
            result.matched.append((i, j, "exact", 1.0))
            exp_open.remove(i)
            ext_open.remove(j)

    # Pass 2 — fuzzy, best pairs first.
    pairs = sorted(
        (
            (text_similarity(expected[i], extracted[j]), i, j)
            for i in exp_open
            for j in ext_open
        ),
        key=lambda t: t[0],
        reverse=True,
    )
    for sim, i, j in pairs:
        if sim < fuzzy_threshold:
            break  # sorted desc — everything after is below the bar too
        if i in exp_open and j in ext_open:
            result.matched.append((i, j, "fuzzy", round(sim, 4)))
            exp_open.remove(i)
            ext_open.remove(j)

    # Pass 3 — optional judge over the ambiguity band.
    if judge is not None:
        band = [
            (sim, i, j)
            for sim, i, j in pairs
            if judge_band_floor <= sim < fuzzy_threshold
        ]
        for sim, i, j in band:
            if i not in exp_open or j not in ext_open:
                continue
            if judge_budget is not None:
                if judge_budget[0] <= 0:
                    result.judge_skipped_over_budget += 1
                    continue
                judge_budget[0] -= 1
            result.judge_calls += 1
            if judge(expected[i], extracted[j]):
                result.judge_accepted += 1
                result.matched.append((i, j, "judge", round(sim, 4)))
                exp_open.remove(i)
                ext_open.remove(j)

    result.unmatched_expected = exp_open
    result.unmatched_extracted = ext_open
    return result


def _normalize_hint(value: Optional[str]) -> str:
    return " ".join((value or "").split()).lower()


def score_action_item_extraction(
    ground_truth: Mapping[str, Any],
    extractions: Mapping[str, Sequence[Mapping[str, Any]]],
    *,
    fuzzy_threshold: float = FUZZY_MATCH_THRESHOLD,
    judge: Optional[Callable[[str, str], bool]] = None,
    max_judge_calls: int = DEFAULT_MAX_JUDGE_CALLS,
) -> dict[str, Any]:
    """Score extracted action items against the labeled corpus.

    ``ground_truth`` maps case-id → entry carrying ``expected`` (a list of
    action-item dicts; empty for hard negatives). ``extractions`` maps the
    same ids → the extracted item dicts (``description`` / ``due_hint`` /
    ``type`` / ``url``, i.e. ``ActionItem.model_dump()`` shape).

    Fail-loud: every labeled case must have an extraction entry (an extractor
    that skipped a case is a defect, not a zero); a labeled entry without an
    ``expected`` list is a corpus defect.

    Returns a quality block whose ``action_items`` key is a
    :class:`~gaia.eval.quality_metrics.Confusion` dict (micro-averaged over
    all cases; TN is structurally 0 for set extraction), plus reported
    secondaries and per-case rows.
    """
    totals = Confusion()
    rows: list[dict[str, Any]] = []
    due_total = 0
    due_correct = 0
    link_total = 0
    link_correct = 0
    negatives_total = 0
    negatives_clean = 0
    judge_calls = 0
    judge_accepted = 0
    judge_skipped = 0
    judge_budget: Optional[list[int]] = [max_judge_calls] if judge is not None else None

    for cid, entry in ground_truth.items():
        if cid in _METADATA_KEYS or not isinstance(entry, dict):
            continue
        expected_items = entry.get("expected")
        if not isinstance(expected_items, list):
            raise ValueError(
                f"ground-truth case {cid!r} has no 'expected' list — a labeled "
                "case must carry its expected action items (empty list for a "
                "hard negative)."
            )
        if cid not in extractions:
            raise ValueError(
                f"no extraction result for labeled case {cid!r} — the "
                "extraction run must cover every case in the corpus."
            )
        extracted_items = list(extractions[cid])

        expected_desc = [str(it["description"]) for it in expected_items]
        extracted_desc = [str(it.get("description", "")) for it in extracted_items]

        match = match_action_items(
            expected_desc,
            extracted_desc,
            fuzzy_threshold=fuzzy_threshold,
            judge=judge,
            judge_budget=judge_budget,
        )
        judge_calls += match.judge_calls
        judge_accepted += match.judge_accepted
        judge_skipped += match.judge_skipped_over_budget

        tp = len(match.matched)
        fn = len(match.unmatched_expected)
        fp = len(match.unmatched_extracted)
        totals.tp += tp
        totals.fn += fn
        totals.fp += fp

        # Secondary: due-hint fidelity over matched pairs whose expected item
        # carries a due hint.
        for i, j, _method, _sim in match.matched:
            exp_hint = expected_items[i].get("due_hint")
            if exp_hint:
                due_total += 1
                if _normalize_hint(exp_hint) == _normalize_hint(
                    extracted_items[j].get("due_hint")
                ):
                    due_correct += 1
            if expected_items[i].get("type") == "link":
                link_total += 1
                if (
                    extracted_items[j].get("type") == "link"
                    and str(extracted_items[j].get("url") or "").strip()
                    == str(expected_items[i].get("url") or "").strip()
                ):
                    link_correct += 1

        is_negative = not expected_items
        if is_negative:
            negatives_total += 1
            if not extracted_items:
                negatives_clean += 1

        rows.append(
            {
                "id": cid,
                "expected": len(expected_items),
                "extracted": len(extracted_items),
                "tp": tp,
                "fp": fp,
                "fn": fn,
                "is_negative": is_negative,
                "match_methods": [m for _i, _j, m, _s in match.matched],
                "missed": [expected_desc[i] for i in match.unmatched_expected],
                "spurious": [extracted_desc[j] for j in match.unmatched_extracted],
            }
        )

    if judge_skipped:
        logger.warning(
            "action-item judge budget exhausted: %d candidate pair(s) were NOT "
            "judged (max_judge_calls=%d). Their cases score as unmatched.",
            judge_skipped,
            max_judge_calls,
        )

    return {
        # Same shape as the existing quality axes (Confusion.to_dict) — the
        # gate and any scorecard consumer read precision/recall/f1 from here.
        "action_items": totals.to_dict(),
        "cases_scored": len(rows),
        "due_hint_accuracy": round(due_correct / due_total, 4) if due_total else 0.0,
        "due_hint_pairs": due_total,
        "link_accuracy": round(link_correct / link_total, 4) if link_total else 0.0,
        "link_pairs": link_total,
        "negatives_total": negatives_total,
        "negatives_clean": negatives_clean,
        "negative_case_fp_rate": (
            round((negatives_total - negatives_clean) / negatives_total, 4)
            if negatives_total
            else 0.0
        ),
        "judge": {
            "enabled": judge is not None,
            "calls": judge_calls,
            "accepted": judge_accepted,
            "skipped_over_budget": judge_skipped,
        },
        "cases": rows,
    }


# ---------------------------------------------------------------------------
# Extraction runner (deterministic, offline — no Lemonade / no LLM)
# ---------------------------------------------------------------------------


def load_action_item_ground_truth(path: str | Path) -> dict[str, Any]:
    """Load the labeled corpus (loud on missing/malformed)."""
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise ValueError(
            f"action-item ground truth at {path} must be a JSON object, got "
            f"{type(data).__name__}."
        )
    labeled = [k for k in data if k not in _METADATA_KEYS]
    if not labeled:
        raise ValueError(f"action-item ground truth at {path} has no labeled cases.")
    return data


def run_action_item_extraction(
    ground_truth: Mapping[str, Any],
) -> dict[str, list[dict[str, Any]]]:
    """Run the production extractor over every labeled case's body.

    Drives ``EmailTriageService._extract_action_items`` — the exact code path
    the REST triage surface uses to populate ``action_items`` (and, via
    ``task_store.record_action_items``, the persistent task list, #1605). The
    extractor is deterministic and needs no LLM, so this runs offline.
    """
    try:
        # The same direct-drive seam the contract unit tests use
        # (tests/unit/agents/email/test_contract_schema.py).
        from gaia_agent_email.api_routes import EmailTriageService
    except ImportError as exc:
        raise RuntimeError(
            "The action-item extraction eval needs the email agent package. "
            "Install it with `pip install gaia-agent-email` (or, in a repo "
            "checkout, `pip install -e hub/agents/python/email`). "
            f"Original import error: {exc}"
        ) from exc

    service = EmailTriageService()
    out: dict[str, list[dict[str, Any]]] = {}
    for cid, entry in ground_truth.items():
        if cid in _METADATA_KEYS or not isinstance(entry, dict):
            continue
        body = entry.get("body")
        if not isinstance(body, str) or not body.strip():
            raise ValueError(
                f"ground-truth case {cid!r} has no non-empty 'body' — nothing "
                "to extract from."
            )
        items = service._extract_action_items(body)  # noqa: SLF001
        out[cid] = [item.model_dump() for item in items]
    return out


# ---------------------------------------------------------------------------
# Gate (same manifest/report pattern as the triage FP/FN + perf gates)
# ---------------------------------------------------------------------------


@dataclass
class ActionItemThresholds:
    """Reported extraction-quality bars + the single ``enforce`` switch.

    ``enforce`` ships ``False`` (report mode): the gate computes and logs but
    never fails CI. Flipping it to ``True`` in the committed manifest (data,
    not code) is what makes a breach block the build — identical to the
    triage quality/perf gates.
    """

    precision_min: float
    recall_min: float
    f1_min: float
    enforce: bool = False


_REQUIRED_ACTION_ITEM_THRESHOLD_KEYS = ("precision_min", "recall_min", "f1_min")


def load_action_item_thresholds(path: str | Path) -> ActionItemThresholds:
    """Load the action-item gate manifest (loud on missing/malformed)."""
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise ValueError(
            f"action-item thresholds manifest at {path} must be a JSON object, "
            f"got {type(data).__name__}."
        )
    missing = [k for k in _REQUIRED_ACTION_ITEM_THRESHOLD_KEYS if k not in data]
    if missing:
        raise ValueError(
            f"action-item thresholds manifest at {path} is missing required "
            f"key(s) {missing}. Required: "
            f"{list(_REQUIRED_ACTION_ITEM_THRESHOLD_KEYS)}; optional: "
            "'enforce' (default false)."
        )
    for k in _REQUIRED_ACTION_ITEM_THRESHOLD_KEYS:
        if not isinstance(data[k], (int, float)) or isinstance(data[k], bool):
            raise ValueError(
                f"action-item threshold '{k}' in {path} must be numeric, got "
                f"{data[k]!r}."
            )
    return ActionItemThresholds(
        precision_min=float(data["precision_min"]),
        recall_min=float(data["recall_min"]),
        f1_min=float(data["f1_min"]),
        enforce=bool(data.get("enforce", False)),
    )


def evaluate_action_item_gate(
    quality: Mapping[str, Any], thresholds: ActionItemThresholds
) -> dict[str, Any]:
    """Compare the ``action_items`` block to the bars.

    Same result shape as ``quality_metrics.evaluate_gate``: ``passed``,
    ``breaches``, ``enforce``, and ``should_fail`` (= enforce and not passed
    — the only hook CI keys off). Fail-loud on a missing metrics block.
    """
    block = quality.get("action_items")
    if not isinstance(block, Mapping):
        raise ValueError(
            "action-item gate needs an 'action_items' confusion block in the "
            f"quality dict (keys: {sorted(quality.keys())})."
        )
    for key in ("precision", "recall", "f1"):
        if key not in block:
            raise ValueError(
                f"action-item quality block is missing '{key}' "
                f"(have: {sorted(block.keys())})."
            )

    precision = float(block["precision"])
    recall = float(block["recall"])
    f1 = float(block["f1"])

    breaches: list[dict[str, Any]] = []
    for metric, value, floor in (
        ("precision", precision, thresholds.precision_min),
        ("recall", recall, thresholds.recall_min),
        ("f1", f1, thresholds.f1_min),
    ):
        if value < floor:
            breaches.append({"metric": metric, "value": value, "min": floor})

    passed = not breaches
    return {
        "axis": "action_items",
        "precision": precision,
        "recall": recall,
        "f1": f1,
        "precision_min": thresholds.precision_min,
        "recall_min": thresholds.recall_min,
        "f1_min": thresholds.f1_min,
        "passed": passed,
        "breaches": breaches,
        "enforce": thresholds.enforce,
        # Report mode (enforce:false) never fails the harness.
        "should_fail": thresholds.enforce and not passed,
    }


# ---------------------------------------------------------------------------
# Optional LLM judge (opt-in, budget-capped — never the primary signal)
# ---------------------------------------------------------------------------


def make_claude_equivalence_judge(
    model: Optional[str] = None,
) -> Callable[[str, str], bool]:
    """Build a Claude-backed equivalence judge for the fuzzy ambiguity band.

    Uses :class:`gaia.eval.claude.ClaudeClient` (the eval framework's judge
    client). Only pairs the deterministic matcher already scored inside
    ``[JUDGE_BAND_FLOOR, FUZZY_MATCH_THRESHOLD)`` reach it, and the corpus
    scorer caps total calls (``max_judge_calls``), so cost is bounded.

    Fail-loud: an unparseable judge reply raises — a judge that can't answer
    must not silently count as a non-match.
    """
    from gaia.eval.claude import ClaudeClient

    client = ClaudeClient(model=model, max_tokens=8)

    def judge(expected: str, extracted: str) -> bool:
        prompt = (
            "Two systems extracted an action item from the same email. Do "
            "these two descriptions refer to the SAME underlying task?\n"
            f"A: {expected}\n"
            f"B: {extracted}\n"
            "Answer with exactly one word: YES or NO."
        )
        blocks = client.get_completion(prompt)
        text = "".join(getattr(b, "text", "") for b in blocks).strip().upper()
        if text.startswith("YES"):
            return True
        if text.startswith("NO"):
            return False
        raise ValueError(
            f"action-item equivalence judge returned an unparseable verdict "
            f"{text!r} (expected YES or NO)."
        )

    return judge


# ---------------------------------------------------------------------------
# Canonical committed-fixture paths (mirrors gaia.eval.benchmark's loaders)
# ---------------------------------------------------------------------------

_FIXTURES_DIR = Path(__file__).resolve().parents[3] / "tests" / "fixtures" / "email"
_GROUND_TRUTH_PATH = _FIXTURES_DIR / "action_items_ground_truth.json"
_THRESHOLDS_PATH = _FIXTURES_DIR / "action_items_gate_thresholds.json"


def default_action_item_ground_truth_path() -> Path:
    """Path to the committed labeled corpus (#1949)."""
    return _GROUND_TRUTH_PATH


def default_action_item_thresholds_path() -> Path:
    """Path to the committed gate manifest (#1949)."""
    return _THRESHOLDS_PATH


def load_default_action_item_thresholds() -> ActionItemThresholds:
    """Load the committed gate manifest (loud if absent/malformed) — the one
    call CI makes to discover the bars and the ``enforce`` switch."""
    return load_action_item_thresholds(default_action_item_thresholds_path())
