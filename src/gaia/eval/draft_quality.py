# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Judge-scored draft-quality eval for the email agent's voice/style drafting
(#1607 feature, #1269 metric, #1948 tracking).

Sibling of :mod:`gaia.eval.benchmark` (the triage benchmark): the same
committed-fixture / report-mode conventions, applied to the drafting path.
Three separable stages so each is testable on its own:

1. **Generation** (:func:`generate_drafts` — needs Lemonade + the email
   agent): per corpus case, build an ``EmailTriageAgent`` over a
   ``FakeGmailBackend`` seeded with the case's incoming email, derive the
   #1607 voice profile from the case's ``sent_history`` (the REAL feature
   path — ``analyze_sent_bodies`` + ``action_store.save_voice_profile``,
   picked up by ``_get_system_prompt``), then ask the agent to draft the
   reply and harvest the composed draft from the fake backend. Drafting
   only — nothing is ever sent.
2. **Judging** (:func:`judge_drafts` — needs a judge callable): score each
   generated draft against the case rubric with an LLM judge
   (:func:`make_claude_judge` wraps :class:`gaia.eval.claude.ClaudeClient`;
   tests inject a stub). The verdict is strict JSON, parsed fail-loud.
3. **Aggregation** (:func:`summarize_drafting`): roll per-case results into
   a ``build_scorecard``-compatible summary with the aggregate
   ``draft_approval_rate``, and score the committed drafting gate manifest
   (``tests/fixtures/email/drafting_gate_thresholds.json``) the same way the
   FP/FN and perf gates work — report mode unless the manifest flips
   ``enforce``.

Fail-loud contract: a malformed corpus, a judge reply that is not the
documented JSON verdict, or a missing thresholds manifest raise actionable
errors — nothing silently scores as a pass.
"""

from __future__ import annotations

import base64
import json
import re
import tempfile
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

_REQUIRED_INCOMING_FIELDS = ("from", "subject", "body")
_MIN_SENT_HISTORY = 3


def _validate_case(case_id: str, case: Any) -> None:
    if not isinstance(case, dict):
        raise ValueError(
            f"drafting corpus case '{case_id}' must be a JSON object, got "
            f"{type(case).__name__}."
        )
    persona = case.get("persona")
    if not isinstance(persona, str) or not persona.strip():
        raise ValueError(f"case '{case_id}': 'persona' must be a non-empty string.")
    incoming = case.get("incoming")
    if not isinstance(incoming, dict):
        raise ValueError(f"case '{case_id}': 'incoming' must be an object.")
    for field in _REQUIRED_INCOMING_FIELDS:
        if not isinstance(incoming.get(field), str) or not incoming[field].strip():
            raise ValueError(
                f"case '{case_id}': incoming.{field} must be a non-empty string."
            )
    history = case.get("sent_history")
    if (
        not isinstance(history, list)
        or len(history) < _MIN_SENT_HISTORY
        or not all(isinstance(b, str) and b.strip() for b in history)
    ):
        raise ValueError(
            f"case '{case_id}': 'sent_history' must be a list of >= "
            f"{_MIN_SENT_HISTORY} non-empty strings (the voice signal the "
            "#1607 profile is derived from)."
        )
    intent = case.get("reply_intent")
    if not isinstance(intent, str) or not intent.strip():
        raise ValueError(
            f"case '{case_id}': 'reply_intent' must be a non-empty string."
        )
    rubric = case.get("rubric")
    if not isinstance(rubric, dict):
        raise ValueError(f"case '{case_id}': 'rubric' must be an object.")
    must = rubric.get("must")
    must_not = rubric.get("must_not")
    if not isinstance(must, list) or not must:
        raise ValueError(
            f"case '{case_id}': rubric.must must be a non-empty list of strings."
        )
    if not isinstance(must_not, list):
        raise ValueError(f"case '{case_id}': rubric.must_not must be a list.")
    for label, items in (("must", must), ("must_not", must_not)):
        if not all(isinstance(i, str) and i.strip() for i in items):
            raise ValueError(
                f"case '{case_id}': rubric.{label} entries must be non-empty "
                "strings."
            )


def _reject_duplicate_keys(pairs: list) -> dict:
    out: dict = {}
    for key, value in pairs:
        if key in out:
            raise ValueError(f"duplicate case id {key!r} in the drafting corpus")
        out[key] = value
    return out


def load_drafting_corpus(path: str | Path) -> dict[str, dict]:
    """Load + validate the drafting corpus (loud on missing/malformed).

    Returns the full mapping including the ``_meta`` block; use
    :func:`corpus_cases` to get only the scored cases. Duplicate case ids,
    missing required fields, or wrong types raise ``ValueError``.
    """
    with open(path, "r", encoding="utf-8") as f:
        try:
            data = json.load(f, object_pairs_hook=_reject_duplicate_keys)
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"drafting corpus at {path} is not valid JSON: {exc}"
            ) from exc
    if not isinstance(data, dict):
        raise ValueError(
            f"drafting corpus at {path} must be a JSON object keyed by case id, "
            f"got {type(data).__name__}."
        )
    cases = {k: v for k, v in data.items() if not k.startswith(_METADATA_PREFIX)}
    if not cases:
        raise ValueError(f"drafting corpus at {path} contains no cases.")
    for case_id, case in cases.items():
        _validate_case(case_id, case)
    return data


def corpus_cases(corpus: Mapping[str, Any]) -> dict[str, dict]:
    """Only the scored cases (drop ``_``-prefixed metadata blocks)."""
    return {k: v for k, v in corpus.items() if not k.startswith(_METADATA_PREFIX)}


# ---------------------------------------------------------------------------
# Judge prompt + verdict parsing (offline)
# ---------------------------------------------------------------------------

# How many sent-history bodies the judge sees as voice exemplars.
_JUDGE_EXEMPLARS = 3

_VERDICT_BOOL_FIELDS = (
    "recipient_and_intent",
    "grounded",
    "no_fabricated_commitments",
    "approved",
)
_VERDICT_SCORE_FIELDS = ("voice_match", "overall_quality")


def build_judge_prompt(case: Mapping[str, Any], draft: Mapping[str, Any]) -> str:
    """Render the judge prompt for one (case, generated draft) pair.

    ``draft`` carries ``to`` / ``subject`` / ``body`` as harvested from the
    backend. The judge sees the incoming email, the user's reply intent, the
    voice exemplars from Sent history, and the case rubric, and must answer
    with STRICT JSON (see :func:`parse_judge_verdict`).
    """
    incoming = case["incoming"]
    exemplars = "\n\n---\n\n".join(case["sent_history"][:_JUDGE_EXEMPLARS])
    must = "\n".join(f"- {item}" for item in case["rubric"]["must"])
    must_not = "\n".join(f"- {item}" for item in case["rubric"]["must_not"])
    return f"""You are grading an email reply drafted by an AI assistant on the user's behalf. The assistant was supposed to write in the user's own voice (learned from their Sent mail) and follow their stated intent.

## Incoming email being replied to
From: {incoming['from']}
Subject: {incoming['subject']}

{incoming['body']}

## What the user asked the assistant to say
{case['reply_intent']}

## The user's own writing (Sent-mail exemplars — this is the voice to match)
{exemplars}

## Rubric — an approvable draft MUST:
{must}

## An approvable draft MUST NOT:
{must_not}

## The assistant's draft
To: {draft.get('to', '')}
Subject: {draft.get('subject', '')}

{draft.get('body', '')}

## Your task
Score the draft. "Approved" means a reasonable user would send this draft after at most trivial edits: it goes to the right recipient, does what the intent asked, is factually grounded in the thread (no invented facts, numbers, dates, or commitments), and sounds like the user's own writing.

Answer with STRICT JSON only — no prose, no markdown fences, exactly this shape:
{{
  "recipient_and_intent": true/false,
  "grounded": true/false,
  "no_fabricated_commitments": true/false,
  "voice_match": 0.0-1.0,
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
        raise ValueError("judge reply is empty — cannot score the draft")
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

    # Judge determinism: generation is temp-0, the judge must be too (#2094).
    client = ClaudeClient(model=model, temperature=0.0)

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


def build_draft_result(
    case_id: str,
    *,
    model_id: str,
    draft: Mapping[str, Any] | None,
    verdict: Mapping[str, Any] | None,
    error: str = "",
    duration_ms: int = 0,
) -> dict[str, Any]:
    """One ``build_scorecard``-compatible result row for a corpus case.

    ``PASS`` = the judge approved the draft; ``FAIL`` = generated (or judged)
    but not approved; ``ERRORED`` = no draft was produced or the judge could
    not score it (``error`` says why). ``overall_score`` maps the judge's
    ``overall_quality`` onto the scorecard's 0–10 scale.
    """
    out: dict[str, Any] = {
        "id": case_id,
        "category": model_id,
        "total_duration_ms": duration_ms,
    }
    if draft is not None:
        out["draft"] = dict(draft)
    if verdict is not None:
        out["draft_judgement"] = dict(verdict)
        out["overall_score"] = round(float(verdict["overall_quality"]) * 10.0, 2)
        out["status"] = "PASS" if verdict["approved"] else "FAIL"
    else:
        out["status"] = "ERRORED"
        out["error"] = error or "no draft produced and no judge verdict"
    return out


def summarize_drafting(
    results: list[dict],
    *,
    run_id: str,
    thresholds: "DraftingThresholds | None" = None,
) -> dict[str, Any]:
    """Aggregate per-case results into a scorecard + drafting-gate summary.

    Reuses :func:`gaia.eval.scorecard.build_scorecard` unchanged. The
    ``drafting`` block carries the #1269 aggregate (``draft_approval_rate``)
    plus reported secondaries; when ``thresholds`` is given the drafting gate
    runs against it — report mode unless the manifest sets ``enforce``. When
    no case was judged the gate is a loud explicit skip, never an invented
    pass.
    """
    from gaia.eval.scorecard import build_scorecard

    scorecard = build_scorecard(run_id, results, {"benchmark": "email_draft_quality"})
    summary: dict[str, Any] = {"scorecard": scorecard}

    judged = [r for r in results if isinstance(r.get("draft_judgement"), dict)]
    aggregate: dict[str, Any] | None = None
    if judged:
        verdicts = [r["draft_judgement"] for r in judged]
        n = len(verdicts)
        aggregate = {
            "cases_total": len(results),
            "cases_judged": n,
            "cases_errored": sum(1 for r in results if r.get("status") == "ERRORED"),
            "draft_approval_rate": round(
                sum(1 for v in verdicts if v["approved"]) / n, 4
            ),
            "voice_match_mean": round(
                sum(float(v["voice_match"]) for v in verdicts) / n, 4
            ),
            "overall_quality_mean": round(
                sum(float(v["overall_quality"]) for v in verdicts) / n, 4
            ),
            "grounded_rate": round(sum(1 for v in verdicts if v["grounded"]) / n, 4),
            "no_fabricated_commitments_rate": round(
                sum(1 for v in verdicts if v["no_fabricated_commitments"]) / n, 4
            ),
            "per_case": [
                {
                    "id": r.get("id"),
                    "status": r.get("status"),
                    "approved": r["draft_judgement"]["approved"],
                    "voice_match": r["draft_judgement"]["voice_match"],
                    "overall_quality": r["draft_judgement"]["overall_quality"],
                    "rationale": r["draft_judgement"]["rationale"],
                }
                for r in judged
            ],
        }
        summary["drafting"] = aggregate

    if thresholds is not None:
        if aggregate is None:
            summary["drafting_gate"] = {
                "skipped": True,
                "reason": (
                    "no case carried a judge verdict; the draft-approval gate "
                    "cannot be evaluated"
                ),
                "enforce": thresholds.enforce,
                "should_fail": False,
            }
        else:
            summary["drafting_gate"] = evaluate_drafting_gate(aggregate, thresholds)

    return summary


# ---------------------------------------------------------------------------
# Committed-threshold gate (#1269 — report mode; flip enforce in the manifest)
# ---------------------------------------------------------------------------


@dataclass
class DraftingThresholds:
    """Draft-approval bar for the drafting gate (#1269 target).

    ``enforce`` is the single safety switch, same contract as the FP/FN and
    perf gates: ``False`` (the committed value) means the gate computes and
    reports but never fails the harness. Flip it in the manifest — data, not
    code — once a real baseline confirms the bar.
    """

    approval_min: float
    enforce: bool = False


def load_drafting_thresholds(path: str | Path) -> DraftingThresholds:
    """Load the drafting-gate thresholds manifest (loud on missing/malformed)."""
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise ValueError(
            f"drafting-gate thresholds manifest at {path} must be a JSON object, "
            f"got {type(data).__name__}."
        )
    if "approval_min" not in data:
        raise ValueError(
            f"drafting-gate thresholds manifest at {path} is missing required "
            "key 'approval_min'. Optional: 'enforce' (default false)."
        )
    bar = data["approval_min"]
    if isinstance(bar, bool) or not isinstance(bar, (int, float)):
        raise ValueError(
            f"drafting-gate threshold 'approval_min' in {path} must be numeric, "
            f"got {bar!r}."
        )
    return DraftingThresholds(
        approval_min=float(bar), enforce=bool(data.get("enforce", False))
    )


def default_drafting_thresholds_path() -> Path:
    """Path to the committed drafting-gate thresholds manifest (#1269/#1948).

    The single entry point CI consumes — flip 'enforce' in that file (data, not
    code) to make CI gate on the #1269 draft-approval bar.
    """
    return resolve_repo_fixture("email", "drafting_gate_thresholds.json")


def load_default_drafting_thresholds() -> DraftingThresholds:
    """Load the committed drafting-gate thresholds (loud if absent/malformed)."""
    return load_drafting_thresholds(default_drafting_thresholds_path())


def evaluate_drafting_gate(
    aggregate: Mapping[str, Any], thresholds: DraftingThresholds
) -> dict[str, Any]:
    """Compare the aggregate ``draft_approval_rate`` to the committed bar.

    Same result shape + ``should_fail`` contract as
    :func:`gaia.eval.quality_metrics.evaluate_gate`: in report mode
    (``enforce=False``) ``should_fail`` is always ``False`` even on a breach.
    Fail-loud on a missing rate — a gate that can't find its input must not
    silently pass.
    """
    if "draft_approval_rate" not in aggregate:
        raise ValueError(
            "drafting gate needs 'draft_approval_rate' in the aggregate block "
            f"(have: {sorted(aggregate.keys())})."
        )
    rate = float(aggregate["draft_approval_rate"])
    breaches: list[dict[str, Any]] = []
    if rate < thresholds.approval_min:
        breaches.append(
            {
                "metric": "draft_approval_rate",
                "value": rate,
                "min": thresholds.approval_min,
            }
        )
    passed = not breaches
    return {
        "metric": "draft_approval_rate",
        "value": rate,
        "min": thresholds.approval_min,
        "passed": passed,
        "breaches": breaches,
        "enforce": thresholds.enforce,
        "should_fail": thresholds.enforce and not passed,
    }


# ---------------------------------------------------------------------------
# Judging stage (offline with an injected judge)
# ---------------------------------------------------------------------------


def judge_drafts(
    corpus: Mapping[str, Any],
    generations: list[dict],
    judge_fn: Callable[[str], str],
    *,
    model_id: str,
) -> list[dict[str, Any]]:
    """Score generated drafts against their case rubrics.

    ``generations`` is :func:`generate_drafts` output (or an equivalent stub):
    ``[{case_id, draft|None, error, duration_ms}]``. A generation that carried
    no draft stays ``ERRORED`` with its generation error; a judge reply that
    cannot be parsed becomes ``ERRORED`` with the parse error — visible in the
    scorecard, never a silent pass or fail.
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
        draft = gen.get("draft")
        duration_ms = int(gen.get("duration_ms", 0))
        if not draft:
            results.append(
                build_draft_result(
                    case_id,
                    model_id=model_id,
                    draft=None,
                    verdict=None,
                    error=gen.get("error") or "agent produced no draft",
                    duration_ms=duration_ms,
                )
            )
            continue
        prompt = build_judge_prompt(cases[case_id], draft)
        try:
            verdict = parse_judge_verdict(judge_fn(prompt))
        except ValueError as exc:
            results.append(
                build_draft_result(
                    case_id,
                    model_id=model_id,
                    draft=draft,
                    verdict=None,
                    error=f"judge verdict unusable: {exc}",
                    duration_ms=duration_ms,
                )
            )
            continue
        results.append(
            build_draft_result(
                case_id,
                model_id=model_id,
                draft=draft,
                verdict=verdict,
                duration_ms=duration_ms,
            )
        )
    return results


# ---------------------------------------------------------------------------
# Generation stage (live — needs Lemonade + the email agent)
# ---------------------------------------------------------------------------


def _incoming_payload(case_id: str, incoming: Mapping[str, str]) -> dict[str, Any]:
    """A Gmail-API-shape message dict for ``FakeGmailBackend.add_message``.

    Mirrors the single-part text/plain shape
    ``tests.fixtures.email.fake_gmail.mbox_message_to_gmail_payload`` emits, so
    the agent's read tools and ``decode_message_body`` handle it identically.
    """
    body = incoming["body"]
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
                {"name": "From", "value": incoming["from"]},
                {"name": "To", "value": "user@example.com"},
                {"name": "Subject", "value": incoming["subject"]},
                {"name": "Message-ID", "value": f"<{case_id}@drafting.eval>"},
            ],
            "body": {"size": len(body.encode("utf-8")), "data": data},
        },
        "sizeEstimate": len(body.encode("utf-8")),
    }


def _newest_draft(backend: Any) -> dict[str, Any] | None:
    """The most recently created draft on a ``FakeGmailBackend`` (or None)."""
    drafts = backend.list_drafts()
    if not drafts:
        return None
    # Draft ids are "draft_<seq>" with a monotonically increasing seq.
    newest = max(drafts, key=lambda d: int(str(d["id"]).rsplit("_", 1)[-1]))
    return {
        "to": newest.get("to", ""),
        "subject": newest.get("subject", ""),
        "body": newest.get("body", ""),
    }


def generate_drafts(
    model_id: str,
    *,
    corpus_path: str | Path,
    base_url: str | None = None,
    db_dir: str | Path | None = None,
    limit: int | None = None,
    agent_factory: Callable[[Any, str], Any] | None = None,
) -> list[dict[str, Any]]:
    """Drive the #1607 drafting path per corpus case and harvest the drafts.

    Per case: seed a fresh ``FakeGmailBackend`` with the incoming email,
    build the agent with a case-scoped SQLite ``db_path``, derive + persist
    the voice profile from the case's ``sent_history`` (the real feature
    path — the profile reaches the LLM via ``_get_system_prompt``), then ask
    the agent to compose the reply with ``draft_reply``. Drafting only: the
    fake backend records the draft; nothing is sent.

    ``agent_factory(backend, db_path)`` overrides agent construction (tests
    inject a stub; keeps the unit path Lemonade-free). Returns
    ``[{case_id, draft|None, error, duration_ms}]`` for :func:`judge_drafts`.
    """
    corpus = load_drafting_corpus(corpus_path)
    cases = corpus_cases(corpus)
    case_items = list(cases.items())
    if limit is not None:
        case_items = case_items[:limit]

    if agent_factory is None:
        # Lazy imports: keep `import gaia.eval.draft_quality` free of the
        # agent stack. EmailTriageAgent ships as the standalone
        # gaia-agent-email wheel (#1102); the fake backend needs a repo
        # checkout.
        try:
            from gaia_agent_email.agent import EmailTriageAgent
            from gaia_agent_email.config import EmailAgentConfig
        except ImportError as exc:
            raise RuntimeError(
                "The drafting eval needs the email agent. Install it with "
                "`pip install gaia-agent-email` (or `pip install "
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
        from gaia_agent_email import action_store
        from gaia_agent_email.voice_profile import analyze_sent_bodies
    except ImportError as exc:
        raise RuntimeError(
            "The drafting eval needs gaia_agent_email.voice_profile (#1607). "
            f"Original import error: {exc}"
        ) from exc
    try:
        from tests.fixtures.email.fake_gmail import FakeGmailBackend
    except ImportError as exc:
        raise RuntimeError(
            "The drafting eval must run from a GAIA repo checkout — it drives "
            "the FakeGmailBackend in tests/fixtures/email and is not available "
            f"in a packaged install. Original import error: {exc}"
        ) from exc

    if db_dir is None:
        db_dir = tempfile.mkdtemp(prefix="gaia-drafting-eval-")
    db_dir = Path(db_dir)
    db_dir.mkdir(parents=True, exist_ok=True)

    generations: list[dict[str, Any]] = []
    for case_id, case in case_items:
        backend = FakeGmailBackend()
        backend.add_message(_incoming_payload(case_id, case["incoming"]))
        agent = agent_factory(backend, str(db_dir / f"{case_id}.db"))

        # The REAL #1607 path: derive the profile from the case's Sent
        # history and persist it; the agent's system prompt picks it up.
        profile = analyze_sent_bodies(case["sent_history"])
        action_store.save_voice_profile(agent, mailbox="google", profile=profile)

        prompt = (
            f"Draft a reply to the email from {case['incoming']['from']} with "
            f"subject '{case['incoming']['subject']}' (message id '{case_id}'). "
            f"The reply should: {case['reply_intent']} "
            "Compose the full reply body yourself in the user's voice and call "
            "draft_reply with it. Create the draft only — do not send anything."
        )
        start = time.monotonic()
        error = ""
        try:
            agent.process_query(prompt)
        except Exception as exc:  # surfaced per-case, never swallowed silently
            error = f"{type(exc).__name__}: {exc}"
        duration_ms = int((time.monotonic() - start) * 1000)

        draft = _newest_draft(backend)
        if draft is None and not error:
            error = "agent finished without creating a draft (draft_reply not called)"
        generations.append(
            {
                "case_id": case_id,
                "draft": draft,
                "error": error,
                "duration_ms": duration_ms,
            }
        )
    return generations
