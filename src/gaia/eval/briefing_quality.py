# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Judge-scored summary-quality eval for the scheduled daily inbox briefing
(#1608 feature, #1951 tracking).

Sibling of :mod:`gaia.eval.draft_quality` and :mod:`gaia.eval.benchmark`: the
same committed-fixture / report-mode conventions, applied to the daily-briefing
surface. The briefing is a pure summarization output — its usefulness lives
entirely in whether it surfaces the right threads, summarizes them faithfully,
groups them by priority, and invents nothing. Unit tests prove the scheduler
*runs*; only a judge can tell whether the digest is any *good*. Three separable
stages so each is testable on its own:

1. **Generation** (:func:`generate_briefings` — needs the email agent): per
   corpus case, seed a fresh ``FakeGmailBackend`` with the case's inbox slice
   and drive the REAL scheduled-briefing path
   (``gaia_agent_email.briefing.run_briefing_job`` → ``pre_scan_inbox_impl``),
   harvesting the exact ``email_pre_scan`` envelope the scheduled job persists.
   Read-only — nothing is sent, archived, or mutated.
2. **Judging** (:func:`judge_briefings` — needs a judge callable): score each
   generated briefing against the case inbox + rubric with an LLM judge
   (:func:`make_claude_judge` wraps :class:`gaia.eval.claude.ClaudeClient`;
   tests inject a stub). The verdict is strict JSON, parsed fail-loud.
3. **Aggregation** (:func:`summarize_briefings`): roll per-case results into a
   ``build_scorecard``-compatible summary with the aggregate
   ``briefing_approval_rate`` plus the faithfulness / must-include-recall /
   hallucination-free secondaries, and score the committed briefing gate
   manifest (``tests/fixtures/email/briefing_gate_thresholds.json``) the same
   way the FP/FN, perf, and drafting gates work — report mode unless the
   manifest flips ``enforce``.

Fail-loud contract: a malformed corpus, a judge reply that is not the
documented JSON verdict, or a missing thresholds manifest raise actionable
errors — nothing silently scores as a pass.
"""

from __future__ import annotations

import base64
import json
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping

from gaia.eval.fixture_paths import resolve_repo_fixture

# ---------------------------------------------------------------------------
# Corpus loading (offline)
# ---------------------------------------------------------------------------

# Keys that are corpus metadata, not cases (same convention as ground_truth).
_METADATA_PREFIX = "_"

_REQUIRED_MESSAGE_FIELDS = ("from", "subject", "body")
_MIN_INBOX_MESSAGES = 3


def _validate_case(case_id: str, case: Any) -> None:
    if not isinstance(case, dict):
        raise ValueError(
            f"briefing corpus case '{case_id}' must be a JSON object, got "
            f"{type(case).__name__}."
        )
    scenario = case.get("scenario")
    if not isinstance(scenario, str) or not scenario.strip():
        raise ValueError(f"case '{case_id}': 'scenario' must be a non-empty string.")
    inbox = case.get("inbox")
    if not isinstance(inbox, list) or len(inbox) < _MIN_INBOX_MESSAGES:
        raise ValueError(
            f"case '{case_id}': 'inbox' must be a list of >= "
            f"{_MIN_INBOX_MESSAGES} messages (the slice the briefing summarizes)."
        )
    for idx, message in enumerate(inbox):
        if not isinstance(message, dict):
            raise ValueError(
                f"case '{case_id}': inbox[{idx}] must be an object, got "
                f"{type(message).__name__}."
            )
        for field in _REQUIRED_MESSAGE_FIELDS:
            if not isinstance(message.get(field), str) or not message[field].strip():
                raise ValueError(
                    f"case '{case_id}': inbox[{idx}].{field} must be a non-empty "
                    "string."
                )
    rubric = case.get("rubric")
    if not isinstance(rubric, dict):
        raise ValueError(f"case '{case_id}': 'rubric' must be an object.")
    must_surface = rubric.get("must_surface")
    if not isinstance(must_surface, list) or not must_surface:
        raise ValueError(
            f"case '{case_id}': rubric.must_surface must be a non-empty list of "
            "strings (the high-priority threads the briefing must surface)."
        )
    must_not_surface = rubric.get("must_not_surface", [])
    if not isinstance(must_not_surface, list):
        raise ValueError(
            f"case '{case_id}': rubric.must_not_surface must be a list (may be "
            "empty)."
        )
    for label, items in (
        ("must_surface", must_surface),
        ("must_not_surface", must_not_surface),
    ):
        if not all(isinstance(i, str) and i.strip() for i in items):
            raise ValueError(
                f"case '{case_id}': rubric.{label} entries must be non-empty "
                "strings."
            )


def _reject_duplicate_keys(pairs: list) -> dict:
    out: dict = {}
    for key, value in pairs:
        if key in out:
            raise ValueError(f"duplicate case id {key!r} in the briefing corpus")
        out[key] = value
    return out


def load_briefing_corpus(path: str | Path) -> dict[str, dict]:
    """Load + validate the briefing corpus (loud on missing/malformed).

    Returns the full mapping including the ``_meta`` block; use
    :func:`corpus_cases` to get only the scored cases. Duplicate case ids,
    missing required fields, or wrong types raise ``ValueError``.
    """
    with open(path, "r", encoding="utf-8") as f:
        try:
            data = json.load(f, object_pairs_hook=_reject_duplicate_keys)
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"briefing corpus at {path} is not valid JSON: {exc}"
            ) from exc
    if not isinstance(data, dict):
        raise ValueError(
            f"briefing corpus at {path} must be a JSON object keyed by case id, "
            f"got {type(data).__name__}."
        )
    cases = {k: v for k, v in data.items() if not k.startswith(_METADATA_PREFIX)}
    if not cases:
        raise ValueError(f"briefing corpus at {path} contains no cases.")
    for case_id, case in cases.items():
        _validate_case(case_id, case)
    return data


def corpus_cases(corpus: Mapping[str, Any]) -> dict[str, dict]:
    """Only the scored cases (drop ``_``-prefixed metadata blocks)."""
    return {k: v for k, v in corpus.items() if not k.startswith(_METADATA_PREFIX)}


# ---------------------------------------------------------------------------
# Judge prompt + verdict parsing (offline)
# ---------------------------------------------------------------------------

_VERDICT_BOOL_FIELDS = (
    "faithful",
    "hallucination_free",
    "grouping_reasonable",
    "approved",
)
_VERDICT_SCORE_FIELDS = ("must_include_recall", "overall_quality")


def _render_inbox(inbox: list[Mapping[str, Any]]) -> str:
    """Render the case's inbox slice as the ground-truth the judge checks
    faithfulness and hallucinations against."""
    blocks = []
    for idx, message in enumerate(inbox, start=1):
        blocks.append(
            f"[{idx}] From: {message['from']}\n"
            f"    Subject: {message['subject']}\n"
            f"    Body: {message['body']}"
        )
    return "\n\n".join(blocks)


def _render_section(items: list[Mapping[str, Any]], reason_key: str) -> str:
    if not items:
        return "  (none)"
    lines = []
    for item in items:
        summary = item.get(reason_key, "") or "(no summary)"
        lines.append(
            f"  - {item.get('sender', '')} — {item.get('subject', '')}: {summary}"
        )
    return "\n".join(lines)


def _render_briefing(briefing: Mapping[str, Any]) -> str:
    """Render a generated ``email_pre_scan`` envelope for the judge."""
    urgent = _render_section(briefing.get("urgent", []), "why")
    actionable = _render_section(briefing.get("actionable", []), "why")
    archives = _render_section(briefing.get("suggested_archives", []), "reason")
    info_count = briefing.get("informational_count", 0)
    return (
        f"URGENT:\n{urgent}\n\n"
        f"ACTIONABLE:\n{actionable}\n\n"
        f"SUGGESTED ARCHIVES:\n{archives}\n\n"
        f"INFORMATIONAL (count only): {info_count}"
    )


def build_judge_prompt(case: Mapping[str, Any], briefing: Mapping[str, Any]) -> str:
    """Render the judge prompt for one (case, generated briefing) pair.

    The judge sees the full inbox slice (the only ground truth for
    faithfulness + hallucination), the generated briefing's sections, and the
    rubric's must-surface / must-not-surface threads, and must answer with
    STRICT JSON (see :func:`parse_judge_verdict`).
    """
    inbox = _render_inbox(case["inbox"])
    briefing_text = _render_briefing(briefing)
    must = "\n".join(f"- {item}" for item in case["rubric"]["must_surface"])
    must_not_items = case["rubric"].get("must_not_surface", [])
    must_not = (
        "\n".join(f"- {item}" for item in must_not_items)
        if must_not_items
        else "(none specified)"
    )
    return f"""You are grading a daily inbox briefing produced by an AI assistant. The assistant scanned the user's inbox and produced a triage digest: threads grouped into URGENT, ACTIONABLE, and SUGGESTED ARCHIVES, plus a count of purely informational mail. A good briefing surfaces the genuinely high-priority threads, summarizes each faithfully, groups them sensibly, and invents nothing that is not in the inbox.

## The user's actual inbox (the ONLY ground truth — every briefing claim must trace back to a message here)
{inbox}

## The assistant's briefing
{briefing_text}

## Rubric — these high-priority threads MUST be surfaced as URGENT or ACTIONABLE:
{must}

## These threads MUST NOT be treated as high-priority (they belong in informational/archives, not urgent/actionable):
{must_not}

## Your task
Judge the briefing against the inbox and rubric:
- faithful: every summary line accurately reflects its source message (no distorted or invented details).
- hallucination_free: the briefing references NO thread, sender, or subject that does not exist in the inbox above.
- grouping_reasonable: threads are placed in sensible priority buckets (nothing important buried, nothing trivial flagged urgent).
- must_include_recall: the fraction (0.0-1.0) of the "MUST be surfaced" threads that actually appear in URGENT or ACTIONABLE.
- overall_quality: your holistic 0.0-1.0 rating of how useful this briefing is to the user.
- approved: true if a reasonable user would trust this briefing to start their day — it surfaces the right threads, summarizes them faithfully, and invents nothing.

Answer with STRICT JSON only — no prose, no markdown fences, exactly this shape:
{{
  "faithful": true/false,
  "hallucination_free": true/false,
  "grouping_reasonable": true/false,
  "must_include_recall": 0.0-1.0,
  "overall_quality": 0.0-1.0,
  "approved": true/false,
  "rationale": "one or two sentences"
}}"""


def parse_judge_verdict(text: str) -> dict[str, Any]:
    """Parse + validate a judge reply into a verdict dict (loud on garbage).

    Tolerates surrounding prose/fences by extracting the outermost JSON
    object, but the object itself must carry every documented field with the
    right type and range — a judge reply that can't be scored is an error,
    never a silent pass or fail.
    """
    if not isinstance(text, str) or not text.strip():
        raise ValueError("judge reply is empty — cannot score the briefing")
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        raise ValueError(
            f"judge reply contains no JSON object; snippet: {text[:200]!r}"
        )
    try:
        verdict = json.loads(match.group(0))
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"judge reply is not valid JSON: {exc}; snippet: {text[:200]!r}"
        ) from exc
    if not isinstance(verdict, dict):
        raise ValueError(
            f"judge verdict decoded to {type(verdict).__name__}, expected object"
        )
    for field in _VERDICT_BOOL_FIELDS:
        if not isinstance(verdict.get(field), bool):
            raise ValueError(
                f"judge verdict field '{field}' must be a boolean, got "
                f"{verdict.get(field)!r}"
            )
    for field in _VERDICT_SCORE_FIELDS:
        value = verdict.get(field)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(
                f"judge verdict field '{field}' must be a number in [0, 1], got "
                f"{value!r}"
            )
        if not 0.0 <= float(value) <= 1.0:
            raise ValueError(
                f"judge verdict field '{field}' out of range [0, 1]: {value!r}"
            )
    if not isinstance(verdict.get("rationale"), str):
        raise ValueError("judge verdict field 'rationale' must be a string")
    return verdict


def make_claude_judge(model: str | None = None) -> Callable[[str], str]:
    """A judge callable backed by :class:`gaia.eval.claude.ClaudeClient`.

    Lazy import so the module stays importable (and unit-testable) without
    the ``[eval]`` extras; ``ClaudeClient`` itself fails loud when the judge
    credential is absent.
    """
    from gaia.eval.claude import ClaudeClient

    client = ClaudeClient(model=model)

    def judge(prompt: str) -> str:
        content = client.get_completion(prompt)
        # Anthropic returns a list of content blocks; the verdict is text.
        parts = [
            getattr(block, "text", "") for block in content if hasattr(block, "text")
        ]
        return "".join(parts)

    return judge


# ---------------------------------------------------------------------------
# Result assembly + aggregation (offline — mocked-judge testable)
# ---------------------------------------------------------------------------


def build_briefing_result(
    case_id: str,
    *,
    model_id: str,
    briefing: Mapping[str, Any] | None,
    verdict: Mapping[str, Any] | None,
    error: str = "",
    duration_ms: int = 0,
) -> dict[str, Any]:
    """One ``build_scorecard``-compatible result row for a corpus case.

    ``PASS`` = the judge approved the briefing; ``FAIL`` = generated (or
    judged) but not approved; ``ERRORED`` = no briefing was produced or the
    judge could not score it (``error`` says why). ``overall_score`` maps the
    judge's ``overall_quality`` onto the scorecard's 0–10 scale.
    """
    out: dict[str, Any] = {
        "id": case_id,
        "category": model_id,
        "total_duration_ms": duration_ms,
    }
    if briefing is not None:
        out["briefing"] = dict(briefing)
    if verdict is not None:
        out["briefing_judgement"] = dict(verdict)
        out["overall_score"] = round(float(verdict["overall_quality"]) * 10.0, 2)
        out["status"] = "PASS" if verdict["approved"] else "FAIL"
    else:
        out["status"] = "ERRORED"
        out["error"] = error or "no briefing produced and no judge verdict"
    return out


def summarize_briefings(
    results: list[dict],
    *,
    run_id: str,
    thresholds: "BriefingThresholds | None" = None,
) -> dict[str, Any]:
    """Aggregate per-case results into a scorecard + briefing-gate summary.

    Reuses :func:`gaia.eval.scorecard.build_scorecard` unchanged. The
    ``briefing`` block carries the aggregate ``briefing_approval_rate`` plus
    the faithfulness / must-include-recall / hallucination-free secondaries;
    when ``thresholds`` is given the briefing gate runs against it — report
    mode unless the manifest sets ``enforce``. When no case was judged the
    gate is a loud explicit skip, never an invented pass.
    """
    from gaia.eval.scorecard import build_scorecard

    scorecard = build_scorecard(
        run_id, results, {"benchmark": "email_briefing_quality"}
    )
    summary: dict[str, Any] = {"scorecard": scorecard}

    judged = [r for r in results if isinstance(r.get("briefing_judgement"), dict)]
    aggregate: dict[str, Any] | None = None
    if judged:
        verdicts = [r["briefing_judgement"] for r in judged]
        n = len(verdicts)
        aggregate = {
            "cases_total": len(results),
            "cases_judged": n,
            "cases_errored": sum(1 for r in results if r.get("status") == "ERRORED"),
            "briefing_approval_rate": round(
                sum(1 for v in verdicts if v["approved"]) / n, 4
            ),
            "faithful_rate": round(sum(1 for v in verdicts if v["faithful"]) / n, 4),
            "hallucination_free_rate": round(
                sum(1 for v in verdicts if v["hallucination_free"]) / n, 4
            ),
            "grouping_reasonable_rate": round(
                sum(1 for v in verdicts if v["grouping_reasonable"]) / n, 4
            ),
            "must_include_recall_mean": round(
                sum(float(v["must_include_recall"]) for v in verdicts) / n, 4
            ),
            "overall_quality_mean": round(
                sum(float(v["overall_quality"]) for v in verdicts) / n, 4
            ),
            "per_case": [
                {
                    "id": r.get("id"),
                    "status": r.get("status"),
                    "approved": r["briefing_judgement"]["approved"],
                    "faithful": r["briefing_judgement"]["faithful"],
                    "hallucination_free": r["briefing_judgement"]["hallucination_free"],
                    "must_include_recall": r["briefing_judgement"][
                        "must_include_recall"
                    ],
                    "overall_quality": r["briefing_judgement"]["overall_quality"],
                    "rationale": r["briefing_judgement"]["rationale"],
                }
                for r in judged
            ],
        }
        summary["briefing"] = aggregate

    if thresholds is not None:
        if aggregate is None:
            # No verdicts at all — a total judge/generation outage. Under an
            # enforcing gate this is a failure, not a free pass: the eval could
            # not establish that the briefing is any good, so the build blocks.
            summary["briefing_gate"] = {
                "skipped": True,
                "reason": (
                    "no case carried a judge verdict; the briefing-quality gate "
                    "could not be evaluated"
                ),
                "enforce": thresholds.enforce,
                "should_fail": thresholds.enforce,
            }
        else:
            summary["briefing_gate"] = evaluate_briefing_gate(aggregate, thresholds)

    return summary


# ---------------------------------------------------------------------------
# Committed-threshold gate (#1951 — report mode; flip enforce in the manifest)
# ---------------------------------------------------------------------------


@dataclass
class BriefingThresholds:
    """Summary-quality bars for the briefing gate (#1951 target).

    ``enforce`` is the single safety switch, same contract as the FP/FN, perf,
    and drafting gates: ``False`` (the committed value) means the gate computes
    and reports but never fails the harness. Flip it in the manifest — data,
    not code — once a real judged baseline confirms the bars. ``recall_min`` /
    ``hallucination_free_min`` / ``faithfulness_min`` default to ``0.0``
    (unset ⇒ not gated) so a manifest may gate on approval alone or add the
    axis floors independently.
    """

    approval_min: float
    recall_min: float = 0.0
    hallucination_free_min: float = 0.0
    faithfulness_min: float = 0.0
    enforce: bool = False


def load_briefing_thresholds(path: str | Path) -> BriefingThresholds:
    """Load the briefing-gate thresholds manifest (loud on missing/malformed)."""
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise ValueError(
            f"briefing-gate thresholds manifest at {path} must be a JSON object, "
            f"got {type(data).__name__}."
        )
    if "approval_min" not in data:
        raise ValueError(
            f"briefing-gate thresholds manifest at {path} is missing required "
            "key 'approval_min'. Optional: 'recall_min', "
            "'hallucination_free_min', 'faithfulness_min', 'enforce' "
            "(default false)."
        )
    for key in (
        "approval_min",
        "recall_min",
        "hallucination_free_min",
        "faithfulness_min",
    ):
        if key in data and (
            isinstance(data[key], bool) or not isinstance(data[key], (int, float))
        ):
            raise ValueError(
                f"briefing-gate threshold '{key}' in {path} must be numeric, "
                f"got {data[key]!r}."
            )
    return BriefingThresholds(
        approval_min=float(data["approval_min"]),
        recall_min=float(data.get("recall_min", 0.0)),
        hallucination_free_min=float(data.get("hallucination_free_min", 0.0)),
        faithfulness_min=float(data.get("faithfulness_min", 0.0)),
        enforce=bool(data.get("enforce", False)),
    )


def default_briefing_thresholds_path() -> Path:
    """Path to the committed briefing-gate thresholds manifest (#1951).

    The single entry point CI consumes — flip 'enforce' in that file (data, not
    code) to make CI gate on the #1951 summary-quality bars.
    """
    return resolve_repo_fixture("email", "briefing_gate_thresholds.json")


def load_default_briefing_thresholds() -> BriefingThresholds:
    """Load the committed briefing-gate thresholds (loud if absent/malformed)."""
    return load_briefing_thresholds(default_briefing_thresholds_path())


def evaluate_briefing_gate(
    aggregate: Mapping[str, Any], thresholds: BriefingThresholds
) -> dict[str, Any]:
    """Compare the aggregate summary-quality metrics to the committed bars.

    Same result shape + ``should_fail`` contract as
    :func:`gaia.eval.quality_metrics.evaluate_gate`: in report mode
    (``enforce=False``) ``should_fail`` is always ``False`` even on a breach.
    Only bars greater than ``0`` are checked (an unset floor is not gated).
    Any errored/unjudged case is itself a breach — a briefing that could not be
    generated or scored is a failure, never silently excluded from the
    denominator. Fail-loud on a missing ``briefing_approval_rate`` — a gate
    that can't find its input must not silently pass.
    """
    if "briefing_approval_rate" not in aggregate:
        raise ValueError(
            "briefing gate needs 'briefing_approval_rate' in the aggregate block "
            f"(have: {sorted(aggregate.keys())})."
        )
    breaches: list[dict[str, Any]] = []
    errored = int(aggregate.get("cases_errored", 0))
    if errored > 0:
        breaches.append({"metric": "cases_errored", "value": errored, "max": 0})
    checks = [
        (
            "briefing_approval_rate",
            float(aggregate["briefing_approval_rate"]),
            thresholds.approval_min,
        ),
        (
            "must_include_recall_mean",
            float(aggregate.get("must_include_recall_mean", 0.0)),
            thresholds.recall_min,
        ),
        (
            "hallucination_free_rate",
            float(aggregate.get("hallucination_free_rate", 0.0)),
            thresholds.hallucination_free_min,
        ),
        (
            "faithful_rate",
            float(aggregate.get("faithful_rate", 0.0)),
            thresholds.faithfulness_min,
        ),
    ]
    for metric, value, minimum in checks:
        if minimum > 0.0 and value < minimum:
            breaches.append({"metric": metric, "value": value, "min": minimum})
    passed = not breaches
    return {
        "metric": "briefing_approval_rate",
        "value": float(aggregate["briefing_approval_rate"]),
        "min": thresholds.approval_min,
        "passed": passed,
        "breaches": breaches,
        "enforce": thresholds.enforce,
        "should_fail": thresholds.enforce and not passed,
    }


# ---------------------------------------------------------------------------
# Judging stage (offline with an injected judge)
# ---------------------------------------------------------------------------


def judge_briefings(
    corpus: Mapping[str, Any],
    generations: list[dict],
    judge_fn: Callable[[str], str],
    *,
    model_id: str,
) -> list[dict[str, Any]]:
    """Score generated briefings against their case inbox + rubric.

    ``generations`` is :func:`generate_briefings` output (or an equivalent
    stub): ``[{case_id, briefing|None, error, duration_ms}]``. A generation
    that carried no briefing stays ``ERRORED`` with its generation error; a
    judge reply that cannot be parsed becomes ``ERRORED`` with the parse error
    — visible in the scorecard, never a silent pass or fail.
    """
    cases = corpus_cases(corpus)
    results: list[dict[str, Any]] = []
    for gen in generations:
        case_id = gen["case_id"]
        if case_id not in cases:
            raise ValueError(
                f"generation references unknown case id {case_id!r} — corpus "
                "and generations are out of sync"
            )
        briefing = gen.get("briefing")
        duration_ms = int(gen.get("duration_ms", 0))
        if not briefing:
            results.append(
                build_briefing_result(
                    case_id,
                    model_id=model_id,
                    briefing=None,
                    verdict=None,
                    error=gen.get("error") or "agent produced no briefing",
                    duration_ms=duration_ms,
                )
            )
            continue
        prompt = build_judge_prompt(cases[case_id], briefing)
        try:
            verdict = parse_judge_verdict(judge_fn(prompt))
        except ValueError as exc:
            results.append(
                build_briefing_result(
                    case_id,
                    model_id=model_id,
                    briefing=briefing,
                    verdict=None,
                    error=f"judge verdict unusable: {exc}",
                    duration_ms=duration_ms,
                )
            )
            continue
        results.append(
            build_briefing_result(
                case_id,
                model_id=model_id,
                briefing=briefing,
                verdict=verdict,
                duration_ms=duration_ms,
            )
        )
    return results


# ---------------------------------------------------------------------------
# Generation stage (drives the REAL scheduled-briefing path)
# ---------------------------------------------------------------------------


def _message_payload(
    case_id: str, index: int, message: Mapping[str, str]
) -> dict[str, Any]:
    """A Gmail-API-shape message dict for ``FakeGmailBackend.add_message``.

    Mirrors the single-part text/plain shape
    ``tests.fixtures.email.fake_gmail.mbox_message_to_gmail_payload`` emits, so
    the briefing path's read tools and ``decode_message_body`` handle it
    identically. ``internalDate`` decreases with ``index`` so the corpus order
    is the newest-first order the briefing scans.
    """
    body = message["body"]
    data = base64.urlsafe_b64encode(body.encode("utf-8")).decode("ascii").rstrip("=")
    msg_id = f"{case_id}-{index:03d}"
    # Later corpus entries are "older": distinct, monotonically decreasing.
    internal_date = str(1751500000000 - index * 60000)
    return {
        "id": msg_id,
        "threadId": msg_id,
        "labelIds": ["INBOX", "UNREAD"],
        "snippet": " ".join(body.split())[:200],
        "internalDate": internal_date,
        "payload": {
            "mimeType": "text/plain",
            "filename": "",
            "headers": [
                {"name": "From", "value": message["from"]},
                {"name": "To", "value": "user@example.com"},
                {"name": "Subject", "value": message["subject"]},
                {"name": "Message-ID", "value": f"<{msg_id}@briefing.eval>"},
            ],
            "body": {"size": len(body.encode("utf-8")), "data": data},
        },
        "sizeEstimate": len(body.encode("utf-8")),
    }


def generate_briefings(
    model_id: str,
    *,
    corpus_path: str | Path,
    max_messages: int = 25,
    limit: int | None = None,
    briefing_fn: Callable[[Any, int], Mapping[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Drive the REAL scheduled-briefing path per corpus case.

    Per case: seed a fresh ``FakeGmailBackend`` with the inbox slice, then run
    the exact scheduled-briefing entry point
    (``gaia_agent_email.briefing.run_briefing_job`` → ``pre_scan_inbox_impl``)
    and harvest the ``email_pre_scan`` envelope it persists. Read-only:
    nothing is sent, archived, or mutated.

    ``briefing_fn(backend, max_messages)`` overrides briefing production (tests
    inject a stub; keeps the unit path dependency-free). ``model_id`` is
    recorded on the result rows for the scorecard even though the current
    briefing path classifies heuristically — it tags which build produced the
    run. Returns ``[{case_id, briefing|None, error, duration_ms}]`` for
    :func:`judge_briefings`.
    """
    corpus = load_briefing_corpus(corpus_path)
    cases = corpus_cases(corpus)
    case_items = list(cases.items())
    if limit is not None:
        case_items = case_items[:limit]

    if briefing_fn is None:
        # Lazy imports: keep `import gaia.eval.briefing_quality` free of the
        # agent stack. The email agent ships as the standalone
        # gaia-agent-email wheel (#1102).
        try:
            from gaia_agent_email.briefing import run_briefing_job
        except ImportError as exc:
            raise RuntimeError(
                "The briefing eval needs the email agent. Install it with "
                "`pip install gaia-agent-email` (or `pip install "
                '"amd-gaia[agents]"`). '
                f"Original import error: {exc}"
            ) from exc

        def briefing_fn(backend: Any, max_msgs: int) -> Mapping[str, Any]:
            captured: dict[str, Any] = {}

            def sink(record: dict[str, Any]) -> None:
                captured["record"] = record

            run_briefing_job(backend, max_messages=max_msgs, sink=sink)
            return captured["record"]["briefing"]

    try:
        from tests.fixtures.email.fake_gmail import FakeGmailBackend
    except ImportError as exc:
        raise RuntimeError(
            "The briefing eval must run from a GAIA repo checkout — it drives "
            "the FakeGmailBackend in tests/fixtures/email and is not available "
            f"in a packaged install. Original import error: {exc}"
        ) from exc

    generations: list[dict[str, Any]] = []
    for case_id, case in case_items:
        backend = FakeGmailBackend()
        for index, message in enumerate(case["inbox"]):
            backend.add_message(_message_payload(case_id, index, message))

        start = time.monotonic()
        error = ""
        briefing: Mapping[str, Any] | None = None
        try:
            briefing = briefing_fn(backend, max_messages)
        except Exception as exc:  # surfaced per-case, never swallowed silently
            error = f"{type(exc).__name__}: {exc}"
        duration_ms = int((time.monotonic() - start) * 1000)

        if briefing is None and not error:
            error = "briefing path returned no envelope"
        generations.append(
            {
                "case_id": case_id,
                "model_id": model_id,
                "briefing": dict(briefing) if briefing is not None else None,
                "error": error,
                "duration_ms": duration_ms,
            }
        )
    return generations


__all__ = [
    "BriefingThresholds",
    "build_briefing_result",
    "build_judge_prompt",
    "corpus_cases",
    "default_briefing_thresholds_path",
    "evaluate_briefing_gate",
    "generate_briefings",
    "judge_briefings",
    "load_briefing_corpus",
    "load_briefing_thresholds",
    "load_default_briefing_thresholds",
    "make_claude_judge",
    "parse_judge_verdict",
    "summarize_briefings",
]
