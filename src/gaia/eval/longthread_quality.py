# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Hermetic scoring for the adversarial long-thread fold corpus (#1889).

Mirrors :mod:`gaia.eval.followup_quality` for the long-thread fixture
(``tests/fixtures/email/longthread_ground_truth.json``): a fail-loud
loader-as-validator, a keyword scorer, and a generation stage that runs the
REAL ``EmailTriageService`` thread-triage path with an injected chat double.

Each corpus case is a deliberately extreme thread — the raw newest-first body
join exceeds ``thread_budget_tokens()`` — whose decisive signal lives ONLY in
the latest message while a conflicting stale signal lives in older messages.
Folding (keep-latest-verbatim + condense-older, #1889) preserves the decisive
signal; a latest-dropping clip would answer with the stale one — so
clipping-vs-folding changes the answer, and the scorer detects which one a
summary reflects.

Hermetic-only in this revision: there is deliberately NO live-runner hook and
NO thresholds/gate manifest — the live-model baseline for this fixture family
lands with the consolidated eval pass (#1319).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable, Mapping

_METADATA_PREFIX = "_"

_REQUIRED_STR_FIELDS = ("scenario", "thread_id", "principal")
_REQUIRED_KEYWORD_FIELDS = ("must_mention", "must_not_mention")


def _validate_message(case_id: str, idx: int, msg: Any) -> None:
    if not isinstance(msg, dict):
        raise ValueError(
            f"case '{case_id}': messages[{idx}] must be an object, got "
            f"{type(msg).__name__}."
        )
    for f in ("from", "subject", "body"):
        if not isinstance(msg.get(f), str) or not msg[f].strip():
            raise ValueError(
                f"case '{case_id}': messages[{idx}].{f} must be a non-empty string."
            )


def _validate_keywords(case_id: str, field: str, value: Any) -> None:
    if (
        not isinstance(value, list)
        or not value
        or not all(isinstance(k, str) and k.strip() for k in value)
    ):
        raise ValueError(
            f"case '{case_id}': '{field}' must be a non-empty list of "
            "non-empty strings."
        )


def _validate_case(case_id: str, case: Any) -> None:
    if not isinstance(case, dict):
        raise ValueError(
            f"long-thread corpus case '{case_id}' must be a JSON object, got "
            f"{type(case).__name__}."
        )
    for f in _REQUIRED_STR_FIELDS:
        if not isinstance(case.get(f), str) or not case[f].strip():
            raise ValueError(f"case '{case_id}': '{f}' must be a non-empty string.")
    messages = case.get("messages")
    if not isinstance(messages, list) or not messages:
        raise ValueError(
            f"case '{case_id}': 'messages' must be a non-empty list of messages."
        )
    for idx, msg in enumerate(messages):
        _validate_message(case_id, idx, msg)
    for f in _REQUIRED_KEYWORD_FIELDS:
        _validate_keywords(case_id, f, case.get(f))
    for f in ("decisive_phrase", "stale_phrase"):
        if not isinstance(case.get(f), str) or not case[f].strip():
            raise ValueError(f"case '{case_id}': '{f}' must be a non-empty string.")

    # The adversarial invariants ARE the corpus contract — a fixture that
    # violates them silently measures nothing, so the loader rejects it loudly.
    last_body = messages[-1]["body"]
    older_bodies = [m["body"] for m in messages[:-1]]
    if case["decisive_phrase"] not in last_body:
        raise ValueError(
            f"case '{case_id}': 'decisive_phrase' must appear in the LAST "
            "message's body — the decisive signal lives only in the latest "
            "message."
        )
    if case["stale_phrase"] in last_body:
        raise ValueError(
            f"case '{case_id}': 'stale_phrase' must NOT appear in the last "
            "message's body — the stale signal must conflict with the latest."
        )
    if not any(case["stale_phrase"] in b for b in older_bodies):
        raise ValueError(
            f"case '{case_id}': 'stale_phrase' must appear in at least one "
            "older message's body."
        )


def _reject_duplicate_keys(pairs: list) -> dict:
    out: dict = {}
    for key, value in pairs:
        if key in out:
            raise ValueError(f"duplicate case id {key!r} in the long-thread corpus")
        out[key] = value
    return out


def load_longthread_corpus(path: str | Path) -> dict[str, dict]:
    """Load + validate the long-thread corpus (loud on missing/malformed).

    Returns the full mapping including the ``_meta`` block; use
    :func:`corpus_cases` for only the scored cases. Duplicate case ids,
    missing required fields, or a violated adversarial invariant (decisive
    phrase not in the last message, stale phrase in the last / absent from
    the older messages) raise ``ValueError``.
    """
    with open(path, "r", encoding="utf-8") as fh:
        try:
            data = json.load(fh, object_pairs_hook=_reject_duplicate_keys)
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"long-thread corpus at {path} is not valid JSON: {exc}"
            ) from exc
    if not isinstance(data, dict):
        raise ValueError(
            f"long-thread corpus at {path} must be a JSON object keyed by "
            f"case id, got {type(data).__name__}."
        )
    cases = {k: v for k, v in data.items() if not k.startswith(_METADATA_PREFIX)}
    if not cases:
        raise ValueError(f"long-thread corpus at {path} contains no cases.")
    for case_id, case in cases.items():
        _validate_case(case_id, case)
    return data


def corpus_cases(corpus: Mapping[str, Any]) -> dict[str, dict]:
    """Only the scored cases (drop ``_``-prefixed metadata blocks)."""
    return {k: v for k, v in corpus.items() if not k.startswith(_METADATA_PREFIX)}


def default_longthread_corpus_path() -> Path:
    """The committed corpus fixture, resolved install-layout-robustly."""
    from gaia.eval.fixture_paths import resolve_repo_fixture

    return resolve_repo_fixture("email", "longthread_ground_truth.json")


# ---------------------------------------------------------------------------
# Scoring (offline)
# ---------------------------------------------------------------------------


def score_case(
    case_id: str,
    case: Mapping[str, Any],
    summary: str | None,
    *,
    model_id: str,
    error: str = "",
    duration_ms: int = 0,
) -> dict[str, Any]:
    """Score one summary into a ``build_scorecard``-compatible result row.

    ``summary`` is what the triage/summarize path produced for the case's
    thread; ``None`` means generation produced nothing usable (``error`` says
    why) and the row is ``ERRORED`` — never silently scored. Otherwise the
    row is ``PASS`` iff every ``must_mention`` keyword appears in the summary
    (case-insensitive substring) and no ``must_not_mention`` keyword does —
    i.e. the summary reflects the latest decisive signal, not the stale one.
    """
    out: dict[str, Any] = {
        "id": case_id,
        "category": model_id,
        "scenario": case.get("scenario", ""),
        "total_duration_ms": duration_ms,
    }
    if summary is None:
        out["status"] = "ERRORED"
        out["error"] = error or "generation produced no summary for the thread"
        return out

    haystack = summary.lower()
    mentioned = [k for k in case["must_mention"] if k.lower() in haystack]
    missing = [k for k in case["must_mention"] if k.lower() not in haystack]
    forbidden_hits = [k for k in case["must_not_mention"] if k.lower() in haystack]
    correct = not missing and not forbidden_hits
    out["longthread_match"] = {
        "mentioned": mentioned,
        "missing": missing,
        "forbidden_hits": forbidden_hits,
        "correct": correct,
    }
    out["overall_score"] = 10.0 if correct else 0.0
    out["status"] = "PASS" if correct else "FAIL"
    return out


def summarize_longthreads(results: list[dict], *, run_id: str) -> dict[str, Any]:
    """Aggregate per-case rows into a scorecard + long-thread summary.

    Reuses :func:`gaia.eval.scorecard.build_scorecard` unchanged. The
    ``longthreads`` aggregate appears only when at least one case was
    actually scored — an all-errored run has NO aggregate rather than an
    invented one. No gate in this revision: the pass bar for this fixture
    family is set by the consolidated eval pass (#1319), not here.
    """
    from gaia.eval.scorecard import build_scorecard

    scorecard = build_scorecard(run_id, results, {"benchmark": "email_longthread_fold"})
    summary: dict[str, Any] = {"scorecard": scorecard}

    scored = [r for r in results if isinstance(r.get("longthread_match"), dict)]
    if scored:
        passed = sum(1 for r in scored if r["longthread_match"]["correct"])
        summary["longthreads"] = {
            "cases_total": len(results),
            "cases_scored": len(scored),
            "cases_errored": sum(1 for r in results if r.get("status") == "ERRORED"),
            "passed": passed,
            "failed": len(scored) - passed,
            "accuracy": round(passed / len(scored), 4),
            "per_case": [
                {
                    "id": r.get("id"),
                    "status": r.get("status"),
                    "correct": r["longthread_match"]["correct"],
                }
                for r in scored
            ],
        }
    return summary


# ---------------------------------------------------------------------------
# Generation stage — REAL thread-triage path, injected chat (offline)
# ---------------------------------------------------------------------------


def build_thread_request(case: Mapping[str, Any]):
    """Build the contract ``EmailTriageRequest`` for a corpus case's thread."""
    # Deferred import: the email agent ships as its own wheel; the corpus and
    # scorer must stay loadable without it (mirror generate_detections).
    from gaia_agent_email.contract import (
        EmailAddress,
        EmailMessage,
        EmailTriageRequest,
        ThreadInput,
    )

    thread_id = case["thread_id"]
    messages = [
        EmailMessage(
            message_id=f"{thread_id}-m{i}",
            thread_id=thread_id,
            subject=m["subject"],
            from_=EmailAddress(email=m["from"]),
            body=m["body"],
        )
        for i, m in enumerate(case["messages"])
    ]
    payload = ThreadInput(
        thread_id=thread_id,
        messages=messages,
        principal=EmailAddress(email=case["principal"]),
    )
    return EmailTriageRequest(payload=payload)


def generate_summaries(
    *,
    corpus_path: str | Path,
    chat_factory: Callable[[Mapping[str, Any]], Any],
) -> list[dict[str, Any]]:
    """Run the REAL ``EmailTriageService`` thread path over every case.

    ``chat_factory(case)`` supplies the chat double (or a live client, for
    the #1319 eval pass) per case. Per-case failures are captured as
    ``{"summary": None, "error": ...}`` rows — one broken case never aborts
    the corpus run, mirroring ``followup_quality.generate_detections``.
    """
    from gaia_agent_email.api_routes import EmailTriageService

    corpus = load_longthread_corpus(corpus_path)
    generations: list[dict[str, Any]] = []
    for case_id, case in corpus_cases(corpus).items():
        try:
            request = build_thread_request(case)
            response = EmailTriageService().triage_request(
                request, chat=chat_factory(case)
            )
            generations.append(
                {"case_id": case_id, "summary": response.result.summary, "error": ""}
            )
        except Exception as exc:  # per-case capture — scored as ERRORED
            generations.append(
                {
                    "case_id": case_id,
                    "summary": None,
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )
    return generations
