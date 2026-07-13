# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Extraction-quality eval for the email agent's action-item extraction
(#1605 feature, #1949 tracking).

Sibling of :mod:`gaia.eval.benchmark` (triage) and :mod:`gaia.eval.draft_quality`
(drafting): the same committed-fixture / report-mode conventions, applied to the
action-item path. The deterministic unit tests for #1605 prove the extractor
*runs*; they say nothing about whether it pulls the *right* tasks. This module
measures that — precision / recall / F1 of the extracted action set against a
hand-labeled corpus, with the hard-negative false-positive case (an email with
no real task) treated as first-class.

Three separable stages, each testable on its own:

1. **Generation** (:func:`generate_extractions` — needs Lemonade + the email
   agent): per corpus case, drive the REAL triage path
   (``triage_inbox`` over a ``FakeGmailBackend`` seeded with the case's email)
   and harvest the ``action_items`` the agent produced.
2. **Matching + scoring** (:func:`score_case`, :func:`match_action_items` —
   fully offline): match each extracted action item against the expected set
   with **fuzzy-primary** matching (normalized char-ratio + token overlap); an
   optional injected LLM judge (:func:`make_claude_judge`) only resolves
   borderline pairs in the gray band. No judge → pure-fuzzy, deterministic.
3. **Aggregation** (:func:`summarize_extraction`): micro-average tp/fp/fn into
   corpus-wide precision / recall / F1, roll per-case rows into a
   ``build_scorecard``-compatible summary, and score the committed gate manifest
   (``action_items_gate_thresholds.json``, ``enforce:false``).

Fail-loud contract: a malformed corpus, a judge reply that is not the documented
yes/no verdict, or a missing thresholds manifest raise actionable errors —
nothing silently scores as a pass.
"""

from __future__ import annotations

import base64
import json
import re
import tempfile
import time
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from gaia.eval.fixture_paths import resolve_repo_fixture
from gaia.eval.quality_metrics import Confusion

# ---------------------------------------------------------------------------
# Corpus loading (offline)
# ---------------------------------------------------------------------------

# Keys that are corpus metadata, not cases (same convention as ground_truth).
_METADATA_PREFIX = "_"

_REQUIRED_EMAIL_FIELDS = ("from", "subject", "body")
_VALID_ITEM_TYPES = {"text", "link"}


def _validate_expected_item(case_id: str, idx: int, item: Any) -> None:
    if not isinstance(item, dict):
        raise ValueError(
            f"case '{case_id}': expected_action_items[{idx}] must be an object, "
            f"got {type(item).__name__}."
        )
    desc = item.get("description")
    if not isinstance(desc, str) or not desc.strip():
        raise ValueError(
            f"case '{case_id}': expected_action_items[{idx}].description must be "
            "a non-empty string."
        )
    item_type = item.get("type", "text")
    if item_type not in _VALID_ITEM_TYPES:
        raise ValueError(
            f"case '{case_id}': expected_action_items[{idx}].type must be one of "
            f"{sorted(_VALID_ITEM_TYPES)}, got {item_type!r}."
        )
    url = item.get("url")
    # Mirror the real ActionItem invariant: url required iff type == 'link'.
    if item_type == "link":
        if not isinstance(url, str) or not url.strip():
            raise ValueError(
                f"case '{case_id}': expected_action_items[{idx}] has type 'link' "
                "but no non-empty 'url'."
            )
    elif url is not None:
        raise ValueError(
            f"case '{case_id}': expected_action_items[{idx}] has type 'text' but "
            "carries a 'url' (url must be null for text actions)."
        )
    due = item.get("due_hint")
    if due is not None and not isinstance(due, str):
        raise ValueError(
            f"case '{case_id}': expected_action_items[{idx}].due_hint must be a "
            "string or absent."
        )


def _validate_case(case_id: str, case: Any) -> None:
    if not isinstance(case, dict):
        raise ValueError(
            f"action-item corpus case '{case_id}' must be a JSON object, got "
            f"{type(case).__name__}."
        )
    scenario = case.get("scenario")
    if not isinstance(scenario, str) or not scenario.strip():
        raise ValueError(f"case '{case_id}': 'scenario' must be a non-empty string.")
    email = case.get("email")
    if not isinstance(email, dict):
        raise ValueError(f"case '{case_id}': 'email' must be an object.")
    for f in _REQUIRED_EMAIL_FIELDS:
        if not isinstance(email.get(f), str) or not email[f].strip():
            raise ValueError(f"case '{case_id}': email.{f} must be a non-empty string.")
    expected = case.get("expected_action_items")
    if not isinstance(expected, list):
        raise ValueError(
            f"case '{case_id}': 'expected_action_items' must be a list (empty for "
            "a hard negative)."
        )
    for idx, item in enumerate(expected):
        _validate_expected_item(case_id, idx, item)
    # 'is_hard_negative' is optional metadata, but if present it must agree with
    # the labeled set — a mislabeled hard negative silently corrupts the FP math.
    hard = case.get("is_hard_negative")
    if hard is not None:
        if not isinstance(hard, bool):
            raise ValueError(f"case '{case_id}': 'is_hard_negative' must be a boolean.")
        if hard != (len(expected) == 0):
            raise ValueError(
                f"case '{case_id}': 'is_hard_negative' is {hard} but "
                f"expected_action_items has {len(expected)} item(s) — the flag "
                "and the labeled set disagree."
            )


def _reject_duplicate_keys(pairs: list) -> dict:
    out: dict = {}
    for key, value in pairs:
        if key in out:
            raise ValueError(f"duplicate case id {key!r} in the action-item corpus")
        out[key] = value
    return out


def load_action_item_corpus(path: str | Path) -> dict[str, dict]:
    """Load + validate the action-item corpus (loud on missing/malformed).

    Returns the full mapping including the ``_meta`` block; use
    :func:`corpus_cases` to get only the scored cases. Duplicate case ids,
    missing required fields, or a hard-negative flag that disagrees with the
    labeled set raise ``ValueError``.
    """
    with open(path, "r", encoding="utf-8") as fh:
        try:
            data = json.load(fh, object_pairs_hook=_reject_duplicate_keys)
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"action-item corpus at {path} is not valid JSON: {exc}"
            ) from exc
    if not isinstance(data, dict):
        raise ValueError(
            f"action-item corpus at {path} must be a JSON object keyed by case id, "
            f"got {type(data).__name__}."
        )
    cases = {k: v for k, v in data.items() if not k.startswith(_METADATA_PREFIX)}
    if not cases:
        raise ValueError(f"action-item corpus at {path} contains no cases.")
    for case_id, case in cases.items():
        _validate_case(case_id, case)
    return data


def corpus_cases(corpus: Mapping[str, Any]) -> dict[str, dict]:
    """Only the scored cases (drop ``_``-prefixed metadata blocks)."""
    return {k: v for k, v in corpus.items() if not k.startswith(_METADATA_PREFIX)}


def is_hard_negative(case: Mapping[str, Any]) -> bool:
    """True when the case has no expected action items (extract-nothing case)."""
    return not case.get("expected_action_items")


# ---------------------------------------------------------------------------
# Fuzzy matching (offline, deterministic)
# ---------------------------------------------------------------------------

# Similarity at/above this counts as a fuzzy match without any judge.
DEFAULT_MATCH_THRESHOLD = 0.5
# Below this, two descriptions are treated as clearly unrelated — no judge is
# consulted (an LLM call there would only add cost and noise).
DEFAULT_JUDGE_FLOOR = 0.3

# Short function words dropped before token-overlap so "reply to Dana" and
# "send Dana a reply" score on content words, not glue.
_STOPWORDS = frozenset(
    {
        "a",
        "an",
        "the",
        "to",
        "of",
        "for",
        "and",
        "or",
        "in",
        "on",
        "at",
        "by",
        "with",
        "is",
        "are",
        "be",
        "your",
        "you",
        "please",
        "so",
        "that",
        "this",
        "it",
        "as",
        "if",
        "we",
        "i",
        "me",
        "my",
        "our",
    }
)

_WORD_RE = re.compile(r"[a-z0-9]+")


def _normalize(text: str) -> str:
    """Lowercase, strip punctuation to spaces, collapse whitespace."""
    return " ".join(_WORD_RE.findall((text or "").lower()))


def _content_tokens(text: str) -> set[str]:
    return {t for t in _normalize(text).split() if t not in _STOPWORDS}


def _token_overlap(a: str, b: str) -> float:
    """Jaccard overlap of content-word sets (order-insensitive)."""
    ta, tb = _content_tokens(a), _content_tokens(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def description_similarity(a: str, b: str) -> float:
    """Fuzzy similarity of two action-item descriptions in ``[0, 1]``.

    The max of a normalized character-sequence ratio (:class:`SequenceMatcher`,
    good at typos/rewordings) and content-token Jaccard overlap (good at
    reordering and partial phrasing). Deterministic — no network, no model.
    """
    na, nb = _normalize(a), _normalize(b)
    if not na or not nb:
        return 0.0
    ratio = SequenceMatcher(None, na, nb).ratio()
    return round(max(ratio, _token_overlap(a, b)), 4)


def _item_description(item: Any) -> str:
    """Pull the description string out of a predicted/expected item.

    Accepts the real ActionItem dict shape ``{description, ...}`` or a bare
    string (a lenient generation path may hand back plain strings).
    """
    if isinstance(item, str):
        return item
    if isinstance(item, Mapping):
        return str(item.get("description", ""))
    raise ValueError(
        f"action item must be a string or an object with 'description', got "
        f"{type(item).__name__}"
    )


@dataclass
class MatchResult:
    """Outcome of matching one email's predicted vs expected action items."""

    matched: list[dict[str, Any]] = field(default_factory=list)
    false_positives: list[int] = field(default_factory=list)  # predicted indices
    false_negatives: list[int] = field(default_factory=list)  # expected indices
    judged_pairs: int = 0  # borderline pairs resolved by the injected judge

    @property
    def tp(self) -> int:
        return len(self.matched)

    @property
    def fp(self) -> int:
        return len(self.false_positives)

    @property
    def fn(self) -> int:
        return len(self.false_negatives)


def match_action_items(
    predicted: Sequence[Any],
    expected: Sequence[Any],
    *,
    threshold: float = DEFAULT_MATCH_THRESHOLD,
    judge_floor: float = DEFAULT_JUDGE_FLOOR,
    judge_fn: Callable[[str, str], bool] | None = None,
) -> MatchResult:
    """Greedy 1:1 match of predicted action items against expected ones.

    Fuzzy-primary: every predicted×expected pair is scored with
    :func:`description_similarity`; pairs are consumed highest-first, each side
    used at most once, and a pair at/above ``threshold`` is a match (true
    positive). Left-over predicted items are false positives; left-over expected
    items are false negatives.

    Minimal-judge: when a top pair falls in the gray band ``[judge_floor,
    threshold)`` and ``judge_fn`` is supplied, the judge is asked whether the two
    describe the same action (``judge_fn(predicted_desc, expected_desc) -> bool``);
    only an affirmative promotes it to a match. With no ``judge_fn`` the band is
    treated as no-match — pure-fuzzy and deterministic. Pairs below
    ``judge_floor`` never reach the judge.
    """
    pred_desc = [_item_description(p) for p in predicted]
    exp_desc = [_item_description(e) for e in expected]

    # Score every candidate pair once, then consume greedily by similarity.
    scored: list[tuple[float, int, int]] = []
    for pi, pd in enumerate(pred_desc):
        for ei, ed in enumerate(exp_desc):
            scored.append((description_similarity(pd, ed), pi, ei))
    # Sort by similarity desc; ties broken by indices for determinism.
    scored.sort(key=lambda t: (-t[0], t[1], t[2]))

    used_pred: set[int] = set()
    used_exp: set[int] = set()
    result = MatchResult()

    for sim, pi, ei in scored:
        if pi in used_pred or ei in used_exp:
            continue
        is_match = False
        if sim >= threshold:
            is_match = True
        elif sim >= judge_floor and judge_fn is not None:
            result.judged_pairs += 1
            is_match = bool(judge_fn(pred_desc[pi], exp_desc[ei]))
        if is_match:
            used_pred.add(pi)
            used_exp.add(ei)
            result.matched.append(
                {
                    "predicted_index": pi,
                    "expected_index": ei,
                    "predicted": pred_desc[pi],
                    "expected": exp_desc[ei],
                    "similarity": round(sim, 4),
                }
            )

    result.false_positives = [i for i in range(len(pred_desc)) if i not in used_pred]
    result.false_negatives = [i for i in range(len(exp_desc)) if i not in used_exp]
    return result


def prf(tp: int, fp: int, fn: int) -> dict[str, float]:
    """Precision / recall / F1 for a set of item counts.

    Hard-negative convention: when there is nothing to find AND nothing was
    predicted (``tp == fp == fn == 0``) the case is a *perfect* extract-nothing
    result — precision/recall/F1 all ``1.0``. This is what makes a correctly
    silent extraction on a no-action email count as a win rather than an
    undefined ``0/0``. Any spurious extraction there (``fp > 0``) drops
    precision as normal.
    """
    if tp == 0 and fp == 0 and fn == 0:
        return {"precision": 1.0, "recall": 1.0, "f1": 1.0}
    precision = tp / (tp + fp) if (tp + fp) else 0.0
    recall = tp / (tp + fn) if (tp + fn) else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0
    return {
        "precision": round(precision, 4),
        "recall": round(recall, 4),
        "f1": round(f1, 4),
    }


# ---------------------------------------------------------------------------
# Per-case scoring → build_scorecard-compatible rows (offline)
# ---------------------------------------------------------------------------


def score_case(
    case_id: str,
    case: Mapping[str, Any],
    predicted: Sequence[Any] | None,
    *,
    model_id: str,
    error: str = "",
    duration_ms: int = 0,
    threshold: float = DEFAULT_MATCH_THRESHOLD,
    judge_floor: float = DEFAULT_JUDGE_FLOOR,
    judge_fn: Callable[[str, str], bool] | None = None,
) -> dict[str, Any]:
    """Score one case into a ``build_scorecard``-compatible result row.

    ``predicted`` is the extracted action-item list (dicts or strings); ``None``
    means the agent produced nothing usable (``error`` says why) and the row is
    ``ERRORED`` — never silently scored. Otherwise the row is ``PASS`` when the
    extraction is perfect for the email (F1 == 1.0: every expected item matched,
    no spurious ones — including "extracted nothing" on a hard negative) and
    ``FAIL`` otherwise. ``overall_score`` maps case F1 onto the 0–10 scorecard
    scale, and ``action_item_match`` carries the tp/fp/fn the aggregator sums.
    """
    out: dict[str, Any] = {
        "id": case_id,
        "category": model_id,
        "scenario": case.get("scenario", ""),
        "hard_negative": is_hard_negative(case),
        "total_duration_ms": duration_ms,
    }
    if predicted is None:
        out["status"] = "ERRORED"
        out["error"] = error or "agent produced no action-item extraction"
        return out

    expected = case.get("expected_action_items", [])
    match = match_action_items(
        predicted,
        expected,
        threshold=threshold,
        judge_floor=judge_floor,
        judge_fn=judge_fn,
    )
    scores = prf(match.tp, match.fp, match.fn)
    out["predicted_action_items"] = [_item_description(p) for p in predicted]
    out["action_item_match"] = {
        "tp": match.tp,
        "fp": match.fp,
        "fn": match.fn,
        "precision": scores["precision"],
        "recall": scores["recall"],
        "f1": scores["f1"],
        "matched": match.matched,
        "false_positive_predictions": [
            _item_description(predicted[i]) for i in match.false_positives
        ],
        "false_negative_expected": [
            _item_description(expected[i]) for i in match.false_negatives
        ],
        "judged_pairs": match.judged_pairs,
    }
    out["overall_score"] = round(scores["f1"] * 10.0, 2)
    out["status"] = "PASS" if scores["f1"] == 1.0 else "FAIL"
    return out


# ---------------------------------------------------------------------------
# Aggregation (offline)
# ---------------------------------------------------------------------------


def summarize_extraction(
    results: list[dict],
    *,
    run_id: str,
    thresholds: "ExtractionThresholds | None" = None,
) -> dict[str, Any]:
    """Aggregate per-case rows into a scorecard + extraction-gate summary.

    Micro-averages tp/fp/fn across every scored case into corpus-wide
    precision / recall / F1 (reusing :class:`gaia.eval.quality_metrics.Confusion`
    for the arithmetic), reports the hard-negative extract-nothing rate
    separately (micro-F1 alone does not reward a correctly silent extraction),
    and reuses :func:`gaia.eval.scorecard.build_scorecard` unchanged. When
    ``thresholds`` is given the extraction gate runs — report mode unless the
    manifest sets ``enforce``. When no case was scored the gate is a loud
    explicit skip, never an invented pass.
    """
    from gaia.eval.scorecard import build_scorecard

    scorecard = build_scorecard(
        run_id, results, {"benchmark": "email_action_item_extraction"}
    )
    summary: dict[str, Any] = {"scorecard": scorecard}

    scored = [r for r in results if isinstance(r.get("action_item_match"), dict)]
    aggregate: dict[str, Any] | None = None
    if scored:
        conf = Confusion()
        hard_total = 0
        hard_correct = 0
        for r in scored:
            m = r["action_item_match"]
            conf.tp += int(m["tp"])
            conf.fp += int(m["fp"])
            conf.fn += int(m["fn"])
            if r.get("hard_negative"):
                hard_total += 1
                if int(m["fp"]) == 0:
                    hard_correct += 1
        aggregate = {
            "cases_total": len(results),
            "cases_scored": len(scored),
            "cases_errored": sum(1 for r in results if r.get("status") == "ERRORED"),
            "total_expected": conf.tp + conf.fn,
            "total_predicted": conf.tp + conf.fp,
            "tp": conf.tp,
            "fp": conf.fp,
            "fn": conf.fn,
            "precision": conf.precision,
            "recall": conf.recall,
            "f1": conf.f1,
            "hard_negatives_total": hard_total,
            "hard_negatives_correct": hard_correct,
            "hard_negative_correct_rate": (
                round(hard_correct / hard_total, 4) if hard_total else 0.0
            ),
            "per_case": [
                {
                    "id": r.get("id"),
                    "status": r.get("status"),
                    "hard_negative": r.get("hard_negative", False),
                    "precision": r["action_item_match"]["precision"],
                    "recall": r["action_item_match"]["recall"],
                    "f1": r["action_item_match"]["f1"],
                }
                for r in scored
            ],
        }
        summary["extraction"] = aggregate

    if thresholds is not None:
        if aggregate is None:
            summary["extraction_gate"] = {
                "skipped": True,
                "reason": (
                    "no case carried an action-item match; the extraction gate "
                    "cannot be evaluated"
                ),
                "enforce": thresholds.enforce,
                "should_fail": False,
            }
        else:
            summary["extraction_gate"] = evaluate_extraction_gate(aggregate, thresholds)

    return summary


# ---------------------------------------------------------------------------
# Committed-threshold gate (#1949 — report mode; flip enforce in the manifest)
# ---------------------------------------------------------------------------


@dataclass
class ExtractionThresholds:
    """Precision/recall/F1 bars for the extraction gate (#1949 target).

    ``enforce`` is the single safety switch, same contract as the FP/FN, perf,
    and drafting gates: ``False`` (the committed value) means the gate computes
    and reports but never fails the harness. Flip it in the manifest — data, not
    code — once a real judged baseline confirms the bars. ``recall_min`` /
    ``precision_min`` default to ``0.0`` (unset ⇒ not gated) so a manifest may
    gate on F1 alone or add the axis floors independently.
    """

    f1_min: float
    recall_min: float = 0.0
    precision_min: float = 0.0
    enforce: bool = False


def load_extraction_thresholds(path: str | Path) -> ExtractionThresholds:
    """Load the extraction-gate thresholds manifest (loud on missing/malformed)."""
    with open(path, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    if not isinstance(data, dict):
        raise ValueError(
            f"extraction-gate thresholds manifest at {path} must be a JSON "
            f"object, got {type(data).__name__}."
        )
    if "f1_min" not in data:
        raise ValueError(
            f"extraction-gate thresholds manifest at {path} is missing required "
            "key 'f1_min'. Optional: 'recall_min', 'precision_min', 'enforce' "
            "(default false)."
        )
    for key in ("f1_min", "recall_min", "precision_min"):
        if key in data and (
            isinstance(data[key], bool) or not isinstance(data[key], (int, float))
        ):
            raise ValueError(
                f"extraction-gate threshold '{key}' in {path} must be numeric, "
                f"got {data[key]!r}."
            )
    return ExtractionThresholds(
        f1_min=float(data["f1_min"]),
        recall_min=float(data.get("recall_min", 0.0)),
        precision_min=float(data.get("precision_min", 0.0)),
        enforce=bool(data.get("enforce", False)),
    )


def default_extraction_thresholds_path() -> Path:
    """Path to the committed extraction-gate thresholds manifest (#1949).

    The single entry point CI consumes — flip 'enforce' in that file (data, not
    code) to make CI gate on the #1949 precision/recall/F1 bars.
    """
    return resolve_repo_fixture("email", "action_items_gate_thresholds.json")


def load_default_extraction_thresholds() -> ExtractionThresholds:
    """Load the committed extraction-gate thresholds (loud if absent/malformed)."""
    return load_extraction_thresholds(default_extraction_thresholds_path())


def evaluate_extraction_gate(
    aggregate: Mapping[str, Any], thresholds: ExtractionThresholds
) -> dict[str, Any]:
    """Compare the aggregate precision/recall/F1 to the committed bars.

    Same result shape + ``should_fail`` contract as
    :func:`gaia.eval.quality_metrics.evaluate_gate`: in report mode
    (``enforce=False``) ``should_fail`` is always ``False`` even on a breach.
    Only bars greater than ``0`` are checked (an unset ``recall_min`` /
    ``precision_min`` is not gated). Fail-loud on a missing ``f1`` — a gate that
    can't find its input must not silently pass.
    """
    if "f1" not in aggregate:
        raise ValueError(
            "extraction gate needs 'f1' in the aggregate block "
            f"(have: {sorted(aggregate.keys())})."
        )
    checks = [
        ("f1", float(aggregate["f1"]), thresholds.f1_min),
        ("recall", float(aggregate.get("recall", 0.0)), thresholds.recall_min),
        ("precision", float(aggregate.get("precision", 0.0)), thresholds.precision_min),
    ]
    breaches: list[dict[str, Any]] = []
    for metric, value, minimum in checks:
        if minimum > 0.0 and value < minimum:
            breaches.append({"metric": metric, "value": value, "min": minimum})
    passed = not breaches
    return {
        "f1": float(aggregate["f1"]),
        "recall": float(aggregate.get("recall", 0.0)),
        "precision": float(aggregate.get("precision", 0.0)),
        "f1_min": thresholds.f1_min,
        "recall_min": thresholds.recall_min,
        "precision_min": thresholds.precision_min,
        "passed": passed,
        "breaches": breaches,
        "enforce": thresholds.enforce,
        "should_fail": thresholds.enforce and not passed,
    }


# ---------------------------------------------------------------------------
# Optional LLM judge for borderline equivalence (minimal, injected)
# ---------------------------------------------------------------------------


def build_equivalence_prompt(predicted_desc: str, expected_desc: str) -> str:
    """Render the yes/no judge prompt for one borderline (predicted, expected)
    pair. Used ONLY when fuzzy similarity is in the gray band."""
    return f"""You are checking whether two short descriptions refer to the SAME action a person should take from an email. Minor wording, tense, or detail differences are fine — judge the underlying task, not the phrasing.

Action A (extracted by an assistant): {predicted_desc}
Action B (the expected action): {expected_desc}

Do A and B describe the same underlying action? Answer with STRICT JSON only, exactly this shape, no prose:
{{"same_action": true}} or {{"same_action": false}}"""


def parse_equivalence_verdict(text: str) -> bool:
    """Parse the judge's yes/no equivalence verdict (loud on garbage)."""
    if not isinstance(text, str) or not text.strip():
        raise ValueError("equivalence judge reply is empty — cannot resolve the pair")
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        raise ValueError(
            f"equivalence judge reply contains no JSON object; snippet: {text[:200]!r}"
        )
    try:
        verdict = json.loads(match.group(0))
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"equivalence judge reply is not valid JSON: {exc}; snippet: {text[:200]!r}"
        ) from exc
    if not isinstance(verdict, dict) or not isinstance(
        verdict.get("same_action"), bool
    ):
        raise ValueError(
            'equivalence judge verdict must be {"same_action": true/false}, got '
            f"{verdict!r}"
        )
    return verdict["same_action"]


def make_claude_judge(model: str | None = None) -> Callable[[str, str], bool]:
    """An equivalence-judge callable backed by :class:`gaia.eval.claude.ClaudeClient`.

    Lazy import so the module stays importable (and unit-testable) without the
    ``[eval]`` extras; ``ClaudeClient`` itself fails loud when the judge
    credential is absent. The returned callable is what :func:`match_action_items`
    consumes as ``judge_fn`` — it renders the equivalence prompt, calls the
    judge, and parses the strict yes/no verdict.
    """
    from gaia.eval.claude import ClaudeClient

    client = ClaudeClient(model=model)

    def judge(predicted_desc: str, expected_desc: str) -> bool:
        prompt = build_equivalence_prompt(predicted_desc, expected_desc)
        content = client.get_completion(prompt)
        parts = [
            getattr(block, "text", "") for block in content if hasattr(block, "text")
        ]
        return parse_equivalence_verdict("".join(parts))

    return judge


# ---------------------------------------------------------------------------
# Generation stage (live — needs Lemonade + the email agent)
# ---------------------------------------------------------------------------


def _incoming_payload(case_id: str, email: Mapping[str, str]) -> dict[str, Any]:
    """A Gmail-API-shape message dict for ``FakeGmailBackend.add_message``.

    Mirrors the single-part text/plain shape used by the drafting eval and the
    fake backend, so the agent's read tools and ``decode_message_body`` handle
    it identically.
    """
    body = email["body"]
    data = base64.urlsafe_b64encode(body.encode("utf-8")).decode("ascii").rstrip("=")
    return {
        "id": case_id,
        "threadId": case_id,
        "labelIds": ["INBOX", "UNREAD"],
        "snippet": " ".join(body.split())[:200],
        "internalDate": "1751500000000",
        "payload": {
            "mimeType": "text/plain",
            "filename": "",
            "headers": [
                {"name": "From", "value": email["from"]},
                {"name": "To", "value": "user@example.com"},
                {"name": "Subject", "value": email["subject"]},
                {"name": "Message-ID", "value": f"<{case_id}@action-items.eval>"},
            ],
            "body": {"size": len(body.encode("utf-8")), "data": data},
        },
        "sizeEstimate": len(body.encode("utf-8")),
    }


def _extract_action_items(agent_result: Any, case_id: str) -> list[dict] | None:
    """Harvest the action items for ``case_id`` from a triage envelope.

    Reuses the triage-benchmark envelope parser: ``triage_inbox`` returns
    ``{ok, data:{results:[{id, category, action_items, ...}]}}`` in a tool
    message. Returns the matching result's ``action_items`` (possibly empty),
    or ``None`` when no triage result for the case was found (an errored run).
    """
    from gaia.eval.benchmark import _extract_triage_results, _normalize_agent_result

    result_dict = _normalize_agent_result(agent_result)
    triage_results, _err = _extract_triage_results(result_dict.get("conversation", []))
    for r in triage_results:
        if r.get("id") == case_id or r.get("message_id") == case_id:
            return list(r.get("action_items", []))
    return None


def generate_extractions(
    model_id: str,
    *,
    corpus_path: str | Path,
    base_url: str | None = None,
    db_dir: str | Path | None = None,
    limit: int | None = None,
    agent_factory: Callable[[Any, str], Any] | None = None,
) -> list[dict[str, Any]]:
    """Drive the REAL triage path per corpus case and harvest action items.

    Per case: seed a fresh ``FakeGmailBackend`` with the case's email, build the
    agent with a case-scoped SQLite ``db_path``, ask it to ``triage_inbox``, and
    pull the extracted ``action_items`` from the triage envelope. Read-only over
    an unchanged agent — nothing is drafted or sent.

    ``agent_factory(backend, db_path)`` overrides agent construction (tests
    inject a stub; keeps the unit path Lemonade-free). Returns
    ``[{case_id, predicted|None, error, duration_ms}]`` for :func:`score_case`.
    """
    corpus = load_action_item_corpus(corpus_path)
    cases = corpus_cases(corpus)
    case_items = list(cases.items())
    if limit is not None:
        case_items = case_items[:limit]

    if agent_factory is None:
        # Lazy imports: keep `import gaia.eval.action_item_quality` free of the
        # agent stack. EmailTriageAgent ships as the standalone
        # gaia-agent-email wheel (#1102); the fake backend needs a checkout.
        try:
            from gaia_agent_email.agent import EmailTriageAgent
            from gaia_agent_email.config import EmailAgentConfig
        except ImportError as exc:
            raise RuntimeError(
                "The action-item extraction eval needs the email agent. Install "
                "it with `pip install gaia-agent-email` (or `pip install "
                '"amd-gaia[agents]"`). '
                f"Original import error: {exc}"
            ) from exc

        def agent_factory(backend: Any, db_path: str) -> Any:
            config = EmailAgentConfig(
                model_id=model_id,
                base_url=base_url,
                gmail_backend=backend,
                db_path=db_path,
                show_stats=True,
                silent_mode=True,
            )
            return EmailTriageAgent(config=config)

    try:
        from tests.fixtures.email.fake_gmail import FakeGmailBackend
    except ImportError as exc:
        raise RuntimeError(
            "The action-item extraction eval must run from a GAIA repo checkout "
            "— it drives the FakeGmailBackend in tests/fixtures/email and is not "
            f"available in a packaged install. Original import error: {exc}"
        ) from exc

    if db_dir is None:
        db_dir = tempfile.mkdtemp(prefix="gaia-action-item-eval-")
    db_dir = Path(db_dir)
    db_dir.mkdir(parents=True, exist_ok=True)

    generations: list[dict[str, Any]] = []
    for case_id, case in case_items:
        backend = FakeGmailBackend()
        backend.add_message(_incoming_payload(case_id, case["email"]))
        agent = agent_factory(backend, str(db_dir / f"{case_id}.db"))

        prompt = (
            "Call triage_inbox for the one message in my inbox and extract its "
            "action items."
        )
        start = time.monotonic()
        error = ""
        predicted: list[dict] | None = None
        try:
            agent_result = agent.process_query(prompt)
            predicted = _extract_action_items(agent_result, case_id)
        except Exception as exc:  # surfaced per-case, never swallowed silently
            error = f"{type(exc).__name__}: {exc}"
        duration_ms = int((time.monotonic() - start) * 1000)

        if predicted is None and not error:
            error = "triage produced no result for the case email"
        generations.append(
            {
                "case_id": case_id,
                "predicted": predicted,
                "error": error,
                "duration_ms": duration_ms,
            }
        )
    return generations


def score_generations(
    corpus: Mapping[str, Any],
    generations: list[dict],
    *,
    model_id: str,
    threshold: float = DEFAULT_MATCH_THRESHOLD,
    judge_floor: float = DEFAULT_JUDGE_FLOOR,
    judge_fn: Callable[[str, str], bool] | None = None,
) -> list[dict[str, Any]]:
    """Score :func:`generate_extractions` output into scorecard-compatible rows.

    ``generations`` is ``[{case_id, predicted|None, error, duration_ms}]`` (real
    or stubbed). A generation that carried no extraction stays ``ERRORED`` with
    its error; everything else is matched + scored via :func:`score_case`.
    """
    cases = corpus_cases(corpus)
    results: list[dict[str, Any]] = []
    for gen in generations:
        case_id = gen["case_id"]
        if case_id not in cases:
            raise ValueError(
                f"generation references unknown case id {case_id!r} — corpus and "
                "generations are out of sync"
            )
        results.append(
            score_case(
                case_id,
                cases[case_id],
                gen.get("predicted"),
                model_id=model_id,
                error=gen.get("error", ""),
                duration_ms=int(gen.get("duration_ms", 0)),
                threshold=threshold,
                judge_floor=judge_floor,
                judge_fn=judge_fn,
            )
        )
    return results
