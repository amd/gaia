# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Detection-quality eval for the email agent's follow-up tracking
(#1606 feature, #1950 tracking).

Sibling of :mod:`gaia.eval.action_item_quality` (extraction) and
:mod:`gaia.eval.draft_quality` (drafting): the same committed-fixture /
report-mode conventions, applied to the follow-up path. The deterministic unit
tests for #1606 (``test_email_followups``) prove the detector *runs*; they say
nothing about whether the threads it flags are the ones a human actually
considers "awaiting a reply". This module measures that — precision / recall /
F1 of the ``awaits_reply`` flag against a hand-labeled corpus of sent threads,
with the two failure modes treated as first-class:

- **False positives** — a thanks/FYI/acknowledgment note the user sent that
  needs no reply, but which the latest-outbound heuristic flags anyway.
- **False negatives** — a genuine question whose latest inbound message is only
  an out-of-office auto-reply, which the detector mistakes for a real answer.

Whether a thread *awaits a reply* is a human judgment (did it actually ask for
something / expect a response). The current detector
(:func:`gaia_agent_email.tools.followup_tools.check_followups_impl`) is
deterministic — latest message outbound + age past the window + an external
recipient. This eval scores that heuristic's classification against the labels,
surfacing exactly where the heuristic and human judgment diverge.

Three separable stages, each testable on its own:

1. **Generation** (:func:`generate_detections` — needs the email agent package
   for the real detector; fully offline, NO Lemonade): per corpus case, replay
   the thread into a ``FakeGmailBackend`` and run the REAL ``check_followups_impl``
   over it, recording whether the case's thread was flagged.
2. **Scoring** (:func:`score_case` — fully offline): compare the predicted flag
   to the labeled ``awaits_reply`` and roll it into a ``build_scorecard``-compatible
   row.
3. **Aggregation** (:func:`summarize_followups`): reduce per-case flags into a
   corpus-wide confusion (:class:`gaia.eval.quality_metrics.Confusion`) →
   precision / recall / F1 / FP-rate / FN-rate, and score the committed gate
   manifest (``followups_gate_thresholds.json``, ``enforce:false``).

Fail-loud contract: a malformed corpus, an unknown case id, or a missing
thresholds manifest raise actionable errors — nothing silently scores as a pass.
"""

from __future__ import annotations

import base64
import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping

from gaia.eval.fixture_paths import resolve_repo_fixture
from gaia.eval.quality_metrics import Confusion

# ---------------------------------------------------------------------------
# Detection parameters (kept in-module so this file is importable without the
# email agent package). window_days matches EmailAgentConfig.followup_window_days
# / DEFAULT_FOLLOWUP_WINDOW_DAYS; the fixed clock keeps corpus ages deterministic.
# ---------------------------------------------------------------------------

DEFAULT_FOLLOWUP_WINDOW_DAYS = 3
# Fixed "now" (epoch ms) so a case's ``age_days`` maps to a stable timestamp.
FIXED_NOW_MS = 1_750_000_000_000
_DAY_MS = 24 * 60 * 60 * 1000
# The corpus principal — outbound mail is authored as coming from this address,
# inbound replies are addressed to it. Matches FakeGmailBackend's default.
CORPUS_USER_EMAIL = "user@example.com"

# Keys that are corpus metadata, not cases (same convention as ground_truth).
_METADATA_PREFIX = "_"

_VALID_DIRECTIONS = {"outbound", "inbound"}


# ---------------------------------------------------------------------------
# Corpus loading (offline)
# ---------------------------------------------------------------------------


def _validate_message(case_id: str, idx: int, msg: Any) -> None:
    if not isinstance(msg, dict):
        raise ValueError(
            f"case '{case_id}': thread[{idx}] must be an object, got "
            f"{type(msg).__name__}."
        )
    direction = msg.get("direction")
    if direction not in _VALID_DIRECTIONS:
        raise ValueError(
            f"case '{case_id}': thread[{idx}].direction must be one of "
            f"{sorted(_VALID_DIRECTIONS)}, got {direction!r}."
        )
    for f in ("subject", "body"):
        if not isinstance(msg.get(f), str) or not msg[f].strip():
            raise ValueError(
                f"case '{case_id}': thread[{idx}].{f} must be a non-empty string."
            )
    age = msg.get("age_days")
    if isinstance(age, bool) or not isinstance(age, (int, float)) or age < 0:
        raise ValueError(
            f"case '{case_id}': thread[{idx}].age_days must be a non-negative "
            f"number, got {age!r}."
        )
    # An outbound message needs a recipient; an inbound one needs a sender.
    if direction == "outbound":
        if not isinstance(msg.get("to"), str) or not msg["to"].strip():
            raise ValueError(
                f"case '{case_id}': thread[{idx}] is outbound but has no non-empty "
                "'to' recipient."
            )
    else:  # inbound
        if not isinstance(msg.get("from"), str) or not msg["from"].strip():
            raise ValueError(
                f"case '{case_id}': thread[{idx}] is inbound but has no non-empty "
                "'from' sender."
            )


def _validate_case(case_id: str, case: Any) -> None:
    if not isinstance(case, dict):
        raise ValueError(
            f"follow-up corpus case '{case_id}' must be a JSON object, got "
            f"{type(case).__name__}."
        )
    scenario = case.get("scenario")
    if not isinstance(scenario, str) or not scenario.strip():
        raise ValueError(f"case '{case_id}': 'scenario' must be a non-empty string.")
    awaits = case.get("awaits_reply")
    if not isinstance(awaits, bool):
        raise ValueError(
            f"case '{case_id}': 'awaits_reply' must be a boolean (the label)."
        )
    thread = case.get("thread")
    if not isinstance(thread, list) or not thread:
        raise ValueError(
            f"case '{case_id}': 'thread' must be a non-empty list of messages."
        )
    for idx, msg in enumerate(thread):
        _validate_message(case_id, idx, msg)
    # A sent thread must contain at least one outbound message, or the Sent-folder
    # scan would never surface it — a corpus case the detector can't reach is a
    # labeling error, not a legitimate hard negative.
    if not any(m.get("direction") == "outbound" for m in thread):
        raise ValueError(
            f"case '{case_id}': thread has no outbound message; a sent-thread "
            "corpus case must contain at least one message from the user."
        )
    # 'is_hard_negative' is optional metadata, but if present it must agree with
    # the label — a mislabeled hard negative silently corrupts the FP math.
    hard = case.get("is_hard_negative")
    if hard is not None:
        if not isinstance(hard, bool):
            raise ValueError(f"case '{case_id}': 'is_hard_negative' must be a boolean.")
        if hard != (not awaits):
            raise ValueError(
                f"case '{case_id}': 'is_hard_negative' is {hard} but 'awaits_reply' "
                f"is {awaits} — the flag and the label disagree (hard negative must "
                "be the not-awaits-reply case)."
            )


def _reject_duplicate_keys(pairs: list) -> dict:
    out: dict = {}
    for key, value in pairs:
        if key in out:
            raise ValueError(f"duplicate case id {key!r} in the follow-up corpus")
        out[key] = value
    return out


def load_followup_corpus(path: str | Path) -> dict[str, dict]:
    """Load + validate the follow-up corpus (loud on missing/malformed).

    Returns the full mapping including the ``_meta`` block; use
    :func:`corpus_cases` to get only the scored cases. Duplicate case ids,
    missing required fields, or an ``is_hard_negative`` flag that disagrees with
    ``awaits_reply`` raise ``ValueError``.
    """
    with open(path, "r", encoding="utf-8") as fh:
        try:
            data = json.load(fh, object_pairs_hook=_reject_duplicate_keys)
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"follow-up corpus at {path} is not valid JSON: {exc}"
            ) from exc
    if not isinstance(data, dict):
        raise ValueError(
            f"follow-up corpus at {path} must be a JSON object keyed by case id, "
            f"got {type(data).__name__}."
        )
    cases = {k: v for k, v in data.items() if not k.startswith(_METADATA_PREFIX)}
    if not cases:
        raise ValueError(f"follow-up corpus at {path} contains no cases.")
    for case_id, case in cases.items():
        _validate_case(case_id, case)
    return data


def corpus_cases(corpus: Mapping[str, Any]) -> dict[str, dict]:
    """Only the scored cases (drop ``_``-prefixed metadata blocks)."""
    return {k: v for k, v in corpus.items() if not k.startswith(_METADATA_PREFIX)}


def is_hard_negative(case: Mapping[str, Any]) -> bool:
    """True when the thread does NOT await a reply (the must-not-flag case)."""
    return not case.get("awaits_reply", False)


# ---------------------------------------------------------------------------
# Thread -> Gmail-API payloads (offline)
# ---------------------------------------------------------------------------


def _message_payload(
    case_id: str, idx: int, msg: Mapping[str, Any], *, now_ms: int
) -> dict[str, Any]:
    """A Gmail-API-shape message dict for ``FakeGmailBackend.add_message``.

    Outbound mail is labeled ``SENT`` (so the Sent-folder scan reaches the
    thread) and authored as coming from the corpus principal; inbound mail is
    labeled ``INBOX`` and addressed to the principal. ``internalDate`` is derived
    from ``age_days`` against the injected ``now_ms`` so ages are deterministic.
    """
    direction = msg["direction"]
    body = msg["body"]
    data = base64.urlsafe_b64encode(body.encode("utf-8")).decode("ascii").rstrip("=")
    if direction == "outbound":
        sender = msg.get("from") or f"Me <{CORPUS_USER_EMAIL}>"
        recipient = msg["to"]
        label_ids = ["SENT"]
    else:
        sender = msg["from"]
        recipient = msg.get("to") or CORPUS_USER_EMAIL
        label_ids = ["INBOX", "UNREAD"]
    internal_date = str(int(now_ms - float(msg["age_days"]) * _DAY_MS))
    return {
        "id": f"{case_id}-{idx}",
        "threadId": case_id,
        "labelIds": label_ids,
        "snippet": " ".join(body.split())[:200],
        "internalDate": internal_date,
        "payload": {
            "mimeType": "text/plain",
            "filename": "",
            "headers": [
                {"name": "From", "value": sender},
                {"name": "To", "value": recipient},
                {"name": "Subject", "value": msg["subject"]},
                {"name": "Date", "value": f"{msg['age_days']} days ago"},
                {"name": "Message-ID", "value": f"<{case_id}-{idx}@followups.eval>"},
            ],
            "body": {"size": len(body.encode("utf-8")), "data": data},
        },
        "sizeEstimate": len(body.encode("utf-8")),
    }


def thread_payloads(
    case_id: str, case: Mapping[str, Any], *, now_ms: int = FIXED_NOW_MS
) -> list[dict[str, Any]]:
    """Render a case's ``thread`` into Gmail-API message payloads (chronological)."""
    return [
        _message_payload(case_id, idx, msg, now_ms=now_ms)
        for idx, msg in enumerate(case["thread"])
    ]


# ---------------------------------------------------------------------------
# Per-case scoring -> build_scorecard-compatible rows (offline)
# ---------------------------------------------------------------------------


def score_case(
    case_id: str,
    case: Mapping[str, Any],
    predicted: bool | None,
    *,
    model_id: str,
    error: str = "",
    duration_ms: int = 0,
) -> dict[str, Any]:
    """Score one case into a ``build_scorecard``-compatible result row.

    ``predicted`` is the detector's ``awaiting_reply`` verdict for the case's
    thread; ``None`` means the detector produced nothing usable (``error`` says
    why) and the row is ``ERRORED`` — never silently scored. Otherwise the row is
    ``PASS`` when the predicted flag matches the labeled ``awaits_reply`` and
    ``FAIL`` otherwise. ``followup_match`` carries the booleans the aggregator
    reduces into a confusion; ``overall_score`` is 10.0 on a correct call, 0.0 on
    a miss.
    """
    expected = bool(case.get("awaits_reply", False))
    out: dict[str, Any] = {
        "id": case_id,
        "category": model_id,
        "scenario": case.get("scenario", ""),
        "awaits_reply_expected": expected,
        "hard_negative": is_hard_negative(case),
        "total_duration_ms": duration_ms,
    }
    if predicted is None:
        out["status"] = "ERRORED"
        out["error"] = error or "detector produced no follow-up verdict"
        return out

    predicted = bool(predicted)
    correct = predicted == expected
    out["followup_match"] = {
        "predicted": predicted,
        "expected": expected,
        "correct": correct,
        # 2x2 cell this case lands in, for readable per-case rows + aggregation.
        "tp": int(predicted and expected),
        "fp": int(predicted and not expected),
        "fn": int((not predicted) and expected),
        "tn": int((not predicted) and (not expected)),
    }
    out["overall_score"] = 10.0 if correct else 0.0
    out["status"] = "PASS" if correct else "FAIL"
    return out


# ---------------------------------------------------------------------------
# Aggregation (offline)
# ---------------------------------------------------------------------------


def summarize_followups(
    results: list[dict],
    *,
    run_id: str,
    thresholds: "FollowupThresholds | None" = None,
) -> dict[str, Any]:
    """Aggregate per-case rows into a scorecard + follow-up-gate summary.

    Reduces the per-case ``awaits_reply`` predictions into a corpus-wide
    confusion (:class:`gaia.eval.quality_metrics.Confusion`) → precision / recall
    / F1 / FP-rate / FN-rate / accuracy, reports the hard-negative correct rate
    separately (the fraction of no-reply-needed threads the detector correctly
    left unflagged — the false-positive story), and reuses
    :func:`gaia.eval.scorecard.build_scorecard` unchanged. When ``thresholds`` is
    given the follow-up gate runs — report mode unless the manifest sets
    ``enforce``. When no case was scored the gate is a loud explicit skip, never
    an invented pass.
    """
    from gaia.eval.scorecard import build_scorecard

    scorecard = build_scorecard(
        run_id, results, {"benchmark": "email_followup_detection"}
    )
    summary: dict[str, Any] = {"scorecard": scorecard}

    scored = [r for r in results if isinstance(r.get("followup_match"), dict)]
    aggregate: dict[str, Any] | None = None
    if scored:
        conf = Confusion()
        hard_total = 0
        hard_correct = 0
        for r in scored:
            m = r["followup_match"]
            conf.tp += int(m["tp"])
            conf.fp += int(m["fp"])
            conf.fn += int(m["fn"])
            conf.tn += int(m["tn"])
            if r.get("hard_negative"):
                hard_total += 1
                # A hard negative is "correct" only when it was NOT flagged (tn).
                if int(m["tn"]) == 1:
                    hard_correct += 1
        aggregate = {
            "cases_total": len(results),
            "cases_scored": len(scored),
            "cases_errored": sum(1 for r in results if r.get("status") == "ERRORED"),
            "awaits_reply_total": conf.tp + conf.fn,
            "flagged_total": conf.tp + conf.fp,
            "tp": conf.tp,
            "fp": conf.fp,
            "fn": conf.fn,
            "tn": conf.tn,
            "precision": conf.precision,
            "recall": conf.recall,
            "f1": conf.f1,
            "false_positive_rate": conf.false_positive_rate,
            "false_negative_rate": conf.false_negative_rate,
            "accuracy": conf.accuracy,
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
                    "predicted": r["followup_match"]["predicted"],
                    "expected": r["followup_match"]["expected"],
                    "correct": r["followup_match"]["correct"],
                }
                for r in scored
            ],
        }
        summary["followups"] = aggregate

    if thresholds is not None:
        if aggregate is None:
            summary["followup_gate"] = {
                "skipped": True,
                "reason": (
                    "no case carried a follow-up verdict; the follow-up gate "
                    "cannot be evaluated"
                ),
                "enforce": thresholds.enforce,
                "should_fail": False,
            }
        else:
            summary["followup_gate"] = evaluate_followup_gate(aggregate, thresholds)

    return summary


# ---------------------------------------------------------------------------
# Committed-threshold gate (#1950 — report mode; flip enforce in the manifest)
# ---------------------------------------------------------------------------


@dataclass
class FollowupThresholds:
    """Precision/recall/F1 bars for the follow-up gate (#1950 target).

    ``enforce`` is the single safety switch, same contract as the FP/FN, perf,
    drafting, and action-item gates: ``False`` (the committed value) means the
    gate computes and reports but never fails the harness. Flip it in the
    manifest — data, not code — once a real baseline confirms the bars.
    ``recall_min`` / ``precision_min`` default to ``0.0`` (unset ⇒ not gated) so a
    manifest may gate on F1 alone or add the axis floors independently.
    """

    f1_min: float
    recall_min: float = 0.0
    precision_min: float = 0.0
    enforce: bool = False


def load_followup_thresholds(path: str | Path) -> FollowupThresholds:
    """Load the follow-up-gate thresholds manifest (loud on missing/malformed)."""
    with open(path, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    if not isinstance(data, dict):
        raise ValueError(
            f"follow-up-gate thresholds manifest at {path} must be a JSON "
            f"object, got {type(data).__name__}."
        )
    if "f1_min" not in data:
        raise ValueError(
            f"follow-up-gate thresholds manifest at {path} is missing required "
            "key 'f1_min'. Optional: 'recall_min', 'precision_min', 'enforce' "
            "(default false)."
        )
    for key in ("f1_min", "recall_min", "precision_min"):
        if key in data and (
            isinstance(data[key], bool) or not isinstance(data[key], (int, float))
        ):
            raise ValueError(
                f"follow-up-gate threshold '{key}' in {path} must be numeric, "
                f"got {data[key]!r}."
            )
    return FollowupThresholds(
        f1_min=float(data["f1_min"]),
        recall_min=float(data.get("recall_min", 0.0)),
        precision_min=float(data.get("precision_min", 0.0)),
        enforce=bool(data.get("enforce", False)),
    )


def default_followup_thresholds_path() -> Path:
    """Path to the committed follow-up-gate thresholds manifest (#1950).

    The single entry point CI consumes — flip 'enforce' in that file (data, not
    code) to make CI gate on the #1950 precision/recall/F1 bars.
    """
    return resolve_repo_fixture("email", "followups_gate_thresholds.json")


def load_default_followup_thresholds() -> FollowupThresholds:
    """Load the committed follow-up-gate thresholds (loud if absent/malformed)."""
    return load_followup_thresholds(default_followup_thresholds_path())


def evaluate_followup_gate(
    aggregate: Mapping[str, Any], thresholds: FollowupThresholds
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
            "follow-up gate needs 'f1' in the aggregate block "
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
# Generation stage (drives the REAL detector — offline, no Lemonade)
# ---------------------------------------------------------------------------


# A detector callable: (backend, *, window_days, now_ms) -> check_followups_impl
# result dict with an "awaiting_reply" list of {thread_id, ...}. Injectable so
# unit tests can drive generation without the email agent package installed.
DetectorFn = Callable[..., Mapping[str, Any]]


def _default_detector(
    backend: Any, *, window_days: int, now_ms: int
) -> Mapping[str, Any]:
    """The real follow-up detector, lazily imported.

    Lazy so ``import gaia.eval.followup_quality`` stays free of the email agent
    stack; ``check_followups_impl`` ships in the standalone gaia-agent-email
    wheel (#1102).
    """
    try:
        from gaia_agent_email.tools.followup_tools import check_followups_impl
    except ImportError as exc:
        raise RuntimeError(
            "The follow-up detection eval needs the email agent. Install it with "
            "`pip install gaia-agent-email` (or `pip install "
            '"amd-gaia[agents]"`). '
            f"Original import error: {exc}"
        ) from exc
    return check_followups_impl(backend, window_days=window_days, now_ms=now_ms)


def generate_detections(
    *,
    corpus_path: str | Path,
    window_days: int = DEFAULT_FOLLOWUP_WINDOW_DAYS,
    now_ms: int = FIXED_NOW_MS,
    limit: int | None = None,
    detector_fn: DetectorFn | None = None,
) -> list[dict[str, Any]]:
    """Run the REAL follow-up detector per corpus case and record its verdict.

    Per case: seed a fresh ``FakeGmailBackend`` with the thread's messages, run
    the detector over its Sent folder, and record whether the case's thread was
    flagged as awaiting a reply. Read-only over an unchanged detector — nothing
    is drafted or sent. Detection is deterministic (no Lemonade, no network).

    ``detector_fn(backend, window_days=..., now_ms=...)`` overrides the detector
    (tests inject a stub; keeps the unit path email-package-free). Returns
    ``[{case_id, predicted|None, error, duration_ms}]`` for :func:`score_case`.
    """
    corpus = load_followup_corpus(corpus_path)
    cases = corpus_cases(corpus)
    case_items = list(cases.items())
    if limit is not None:
        case_items = case_items[:limit]

    detector = detector_fn or _default_detector

    try:
        from tests.fixtures.email.fake_gmail import FakeGmailBackend
    except ImportError as exc:
        raise RuntimeError(
            "The follow-up detection eval must run from a GAIA repo checkout — it "
            "drives the FakeGmailBackend in tests/fixtures/email and is not "
            f"available in a packaged install. Original import error: {exc}"
        ) from exc

    generations: list[dict[str, Any]] = []
    for case_id, case in case_items:
        backend = FakeGmailBackend(user_email=CORPUS_USER_EMAIL)
        for payload in thread_payloads(case_id, case, now_ms=now_ms):
            backend.add_message(payload)

        start = time.monotonic()
        error = ""
        predicted: bool | None = None
        try:
            result = detector(backend, window_days=window_days, now_ms=now_ms)
            flagged = {
                item.get("thread_id") for item in result.get("awaiting_reply", [])
            }
            predicted = case_id in flagged
        except Exception as exc:  # surfaced per-case, never swallowed silently
            error = f"{type(exc).__name__}: {exc}"
        duration_ms = int((time.monotonic() - start) * 1000)

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
) -> list[dict[str, Any]]:
    """Score :func:`generate_detections` output into scorecard-compatible rows.

    ``generations`` is ``[{case_id, predicted|None, error, duration_ms}]`` (real
    or stubbed). A generation that carried no verdict stays ``ERRORED`` with its
    error; everything else is scored via :func:`score_case`.
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
            )
        )
    return results
