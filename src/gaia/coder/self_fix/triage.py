# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Feedback triage (§7.4 step 1) and localisation (§7.4 step 2).

Triage is LLM-driven via Claude Opus 4.7 using ``prompts/triage.md`` (§15.8 P1).
Localisation is deterministic — a grep across the triage-proposed candidate
files to produce concrete ``file:line-range`` hits for the planner.

The LLM call site is abstracted via a :class:`TriageClient` protocol so tests
can inject a mock without touching any real Anthropic client.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, List, Mapping, Optional, Protocol, Sequence

logger = logging.getLogger(__name__)

#: Canonical set of the eight fix-class labels (§7.4 step 1).
FIX_CLASSES: tuple[str, ...] = (
    "prompt",
    "doc",
    "test",
    "tool",
    "policy",
    "architectural",
    "state-machine",
    "out-of-scope",
)

#: Threshold below which a triage result is rewritten to ``out-of-scope`` so
#: the loop never commits to a guess. Mirrors §7.2's low-confidence discard
#: rule.
LOW_CONFIDENCE_ESCALATION_THRESHOLD: int = 60


# ---------------------------------------------------------------------------
# Result dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CandidateFile:
    """One ``{path, why}`` entry from the triage classifier."""

    path: str
    why: str


@dataclass(frozen=True)
class FixClassResult:
    """Structured result of :func:`classify_fix_class`.

    Matches the JSON schema the LLM must return per prompt P1 (§15.8), with
    the addition of a boolean :attr:`escalated_low_confidence` flag set to
    ``True`` when the classifier's confidence fell below
    :data:`LOW_CONFIDENCE_ESCALATION_THRESHOLD` and the fix class was
    rewritten to ``out-of-scope``.
    """

    fix_class: str
    root_cause_hypothesis: str
    candidate_files: tuple[CandidateFile, ...]
    prior_pattern_hit: Optional[str]
    confidence: int
    escalated_low_confidence: bool = False


@dataclass(frozen=True)
class LocalisationHit:
    """One grep hit produced by :func:`localise`."""

    path: str
    line_start: int
    line_end: int
    snippet: str


# ---------------------------------------------------------------------------
# LLM protocol (mockable in tests)
# ---------------------------------------------------------------------------


class TriageClient(Protocol):
    """Callable that runs the triage prompt and returns the raw JSON string."""

    def __call__(
        self,
        *,
        prompt: str,
        feedback_id: str,
        feedback_body: str,
        context_json: Mapping[str, Any],
    ) -> str: ...


TriageClientFn = Callable[..., str]


#: Token cap for the P1 triage prompt. Spec §15.8 reserves 2k for the
#: classifier's JSON envelope; tests can shrink this by injecting a custom
#: client.
_TRIAGE_MAX_TOKENS: int = 2048


def _default_triage_client(*, prompt: str, **_kwargs: Any) -> str:
    """Run the triage prompt through :class:`gaia.coder.llm.CoderLLM`.

    The :class:`TriageClient` protocol passes structured kwargs
    (``feedback_id``, ``feedback_body``, ``context_json``) for clients that
    want to log them — the rendered ``prompt`` already contains those
    fields, so this default ignores them and just forwards the prompt.

    Raises:
        RuntimeError: when the ``anthropic`` SDK is not installed. The
            module stays importable on stores-only CI lines because
            :class:`CoderLLM` is constructed lazily inside the helper.
            Other failures (missing ``ANTHROPIC_API_KEY``, transport
            errors) propagate per the fail-loudly rule in ``CLAUDE.md``.
    """
    # Imported lazily so importing :mod:`gaia.coder.self_fix.triage` on a
    # box without the anthropic SDK does not fail at module import time —
    # only callers that actually run the default pay the import cost.
    from gaia.coder.llm import default_completion_client

    return default_completion_client(prompt=prompt, max_tokens=_TRIAGE_MAX_TOKENS)


# ---------------------------------------------------------------------------
# Prompt loading
# ---------------------------------------------------------------------------


_PROMPTS_DIR = Path(__file__).resolve().parent.parent / "prompts"


def _load_prompt(name: str) -> str:
    """Read a prompt template from ``src/gaia/coder/prompts/``."""
    path = _PROMPTS_DIR / name
    if not path.exists():
        raise FileNotFoundError(f"prompt template missing: {path}")
    return path.read_text(encoding="utf-8")


def _render_prompt(
    template: str,
    *,
    feedback_id: str,
    iso8601: str,
    em_handle: str,
    severity: str,
    context_url: Optional[str],
    body: str,
    failure_pattern_hits: Sequence[Mapping[str, Any]],
) -> str:
    """Substitute the ``{{...}}`` slots used by ``prompts/triage.md`` (P1).

    Non-LLM prompt assembly — deterministic string templating. The P1 template
    uses double-brace placeholders (``{{feedback_id}}`` etc.) rather than
    Python ``str.format`` to keep the file friendly for humans to edit.
    """
    rendered = template
    subs: dict[str, str] = {
        "feedback_id": feedback_id,
        "iso8601": iso8601,
        "em_handle": em_handle,
        "low|med|high|critical": severity,
        "url_or_none": context_url or "null",
        "raw_body": body,
        "top_3_similar_past_failures_json": json.dumps(
            list(failure_pattern_hits or [])
        ),
    }
    for key, value in subs.items():
        rendered = rendered.replace("{{" + key + "}}", value)
    return rendered


# ---------------------------------------------------------------------------
# Triage
# ---------------------------------------------------------------------------


@dataclass
class TriageContext:
    """Extra context the triage classifier needs beyond the feedback body."""

    feedback_id: str
    received_at: str
    from_handle: str
    severity: str
    context_url: Optional[str] = None
    failure_pattern_hits: tuple[Mapping[str, Any], ...] = field(default_factory=tuple)


def classify_fix_class(
    feedback_body: str,
    context: TriageContext,
    *,
    client: Optional[TriageClientFn] = None,
) -> FixClassResult:
    """Classify an EM feedback record into one of the eight fix classes.

    Invokes the P1 prompt (§15.8) on Claude Opus 4.7 at temperature=0. Tests
    mock ``client`` with a callable returning the canonical JSON payload.

    Raises:
        ValueError: when the LLM response fails to parse, when the returned
            ``fix_class`` is not in :data:`FIX_CLASSES`, or when the returned
            ``confidence`` is out of the ``[0, 100]`` range. Fail-loudly per
            ``CLAUDE.md`` — a corrupt triage output must not silently fall
            through to a downstream fix.

    Confidence handling: the classifier's decision is preserved in
    :attr:`FixClassResult.fix_class` for classes other than ``out-of-scope``
    **only when** ``confidence >= 60``. Below the threshold the fix_class is
    rewritten to ``out-of-scope`` and :attr:`escalated_low_confidence` is
    ``True``. Original classifier output is still readable via the
    ``root_cause_hypothesis``.
    """
    client = client or _default_triage_client
    template = _load_prompt("triage.md")
    prompt = _render_prompt(
        template,
        feedback_id=context.feedback_id,
        iso8601=context.received_at,
        em_handle=context.from_handle,
        severity=context.severity,
        context_url=context.context_url,
        body=feedback_body,
        failure_pattern_hits=context.failure_pattern_hits,
    )
    raw = client(
        prompt=prompt,
        feedback_id=context.feedback_id,
        feedback_body=feedback_body,
        context_json={
            "received_at": context.received_at,
            "from_handle": context.from_handle,
            "severity": context.severity,
            "context_url": context.context_url,
            "failure_pattern_hits": list(context.failure_pattern_hits),
        },
    )
    return _parse_triage_response(raw)


def _parse_triage_response(raw: str) -> FixClassResult:
    """Parse the raw LLM JSON response into :class:`FixClassResult`."""
    if not isinstance(raw, str) or not raw.strip():
        raise ValueError("triage response was empty")
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"triage response is not valid JSON: {exc}") from exc
    if not isinstance(parsed, dict):
        raise ValueError("triage response is not a JSON object")

    fix_class = parsed.get("fix_class")
    if fix_class not in FIX_CLASSES:
        raise ValueError(
            f"triage returned unknown fix_class {fix_class!r}; "
            f"expected one of {FIX_CLASSES}"
        )
    confidence = parsed.get("confidence")
    if not isinstance(confidence, int) or not 0 <= confidence <= 100:
        raise ValueError(
            f"triage returned invalid confidence {confidence!r}; "
            "expected int in [0, 100]"
        )
    root_cause = parsed.get("root_cause_hypothesis") or ""
    if not isinstance(root_cause, str):
        raise ValueError("triage root_cause_hypothesis must be a string")
    candidate_files_raw = parsed.get("candidate_files") or []
    if not isinstance(candidate_files_raw, list):
        raise ValueError("triage candidate_files must be a list")
    candidates: List[CandidateFile] = []
    for entry in candidate_files_raw:
        if not isinstance(entry, dict):
            raise ValueError("candidate_files entry must be an object")
        path = entry.get("path")
        why = entry.get("why", "")
        if not isinstance(path, str) or not path:
            raise ValueError("candidate_files entry missing 'path'")
        candidates.append(CandidateFile(path=path, why=str(why)))
    prior = parsed.get("prior_pattern_hit")
    if prior is not None and not isinstance(prior, str):
        raise ValueError("prior_pattern_hit must be a string or null")

    escalated = False
    if fix_class != "out-of-scope" and confidence < LOW_CONFIDENCE_ESCALATION_THRESHOLD:
        fix_class = "out-of-scope"
        escalated = True
        logger.info(
            "triage escalated to out-of-scope (confidence=%d < %d)",
            confidence,
            LOW_CONFIDENCE_ESCALATION_THRESHOLD,
        )

    return FixClassResult(
        fix_class=fix_class,
        root_cause_hypothesis=root_cause,
        candidate_files=tuple(candidates),
        prior_pattern_hit=prior,
        confidence=confidence,
        escalated_low_confidence=escalated,
    )


# ---------------------------------------------------------------------------
# Localisation (deterministic grep)
# ---------------------------------------------------------------------------


_LINE_RANGE_RE = re.compile(r"^(?P<path>.+?)(?::(?P<start>\d+)(?:-(?P<end>\d+))?)?$")


def _split_path_and_range(
    path_or_range: str,
) -> tuple[str, Optional[int], Optional[int]]:
    """Split ``"foo/bar.py:10-20"`` into ``("foo/bar.py", 10, 20)``.

    Without a range returns ``(path, None, None)``. Returns a single-line
    range when only ``start`` is present.
    """
    match = _LINE_RANGE_RE.match(path_or_range.strip())
    if match is None:
        return path_or_range, None, None
    raw_start = match.group("start")
    raw_end = match.group("end")
    start = int(raw_start) if raw_start else None
    end = int(raw_end) if raw_end else start
    return match.group("path"), start, end


def localise(
    fix_class: str,
    candidate_files: Sequence[CandidateFile],
    *,
    repo_root: Optional[Path] = None,
    keywords: Sequence[str] = (),
    max_hits: int = 20,
) -> List[LocalisationHit]:
    """Produce :class:`LocalisationHit`s for each candidate under ``repo_root``.

    For each :class:`CandidateFile`:

    * If the ``path`` carries a ``:line`` or ``:start-end`` suffix, the exact
      range is extracted from the file on disk and returned verbatim.
    * Otherwise, every line containing any of ``keywords`` (or the first
      non-empty line if ``keywords`` is empty) is returned as a single-line
      hit.

    The caller supplies ``keywords`` extracted from the feedback body or the
    fix_class-specific vocabulary. Deterministic — no LLM. Missing files are
    logged at INFO and skipped (a classifier proposal that no longer exists
    is a symptom, not a crash).

    ``fix_class`` is accepted for future-proofing (e.g. to restrict the glob
    to ``*.md`` for ``prompt`` / ``doc`` classes). Phase 6 uses it only to
    tag the logger.
    """
    root = Path(repo_root) if repo_root else Path.cwd()
    hits: List[LocalisationHit] = []
    for candidate in candidate_files:
        path, start, end = _split_path_and_range(candidate.path)
        abs_path = root / path if not Path(path).is_absolute() else Path(path)
        if not abs_path.exists() or not abs_path.is_file():
            logger.info(
                "localise(%s): candidate %s does not exist under %s",
                fix_class,
                candidate.path,
                root,
            )
            continue
        try:
            text = abs_path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError) as exc:
            logger.info(
                "localise(%s): skipping %s (%s)", fix_class, candidate.path, exc
            )
            continue
        lines = text.splitlines()
        total = len(lines)
        if start is not None:
            end = end or start
            clamped_end = min(end, total)
            clamped_start = max(1, start)
            snippet = "\n".join(lines[clamped_start - 1 : clamped_end])
            hits.append(
                LocalisationHit(
                    path=path,
                    line_start=clamped_start,
                    line_end=clamped_end,
                    snippet=snippet,
                )
            )
            if len(hits) >= max_hits:
                break
            continue
        # No explicit range — search for keywords (case-insensitive) or
        # fall back to the first non-blank line.
        found = False
        lowered_keywords = [kw.lower() for kw in keywords if kw]
        for lineno, line in enumerate(lines, start=1):
            if lowered_keywords and not any(
                kw in line.lower() for kw in lowered_keywords
            ):
                continue
            hits.append(
                LocalisationHit(
                    path=path,
                    line_start=lineno,
                    line_end=lineno,
                    snippet=line,
                )
            )
            found = True
            if len(hits) >= max_hits:
                break
        if not found and not lowered_keywords:
            # Fall-back: first non-blank line.
            for lineno, line in enumerate(lines, start=1):
                if line.strip():
                    hits.append(
                        LocalisationHit(
                            path=path,
                            line_start=lineno,
                            line_end=lineno,
                            snippet=line,
                        )
                    )
                    break
        if len(hits) >= max_hits:
            break
    return hits


__all__ = [
    "CandidateFile",
    "FixClassResult",
    "FIX_CLASSES",
    "LOW_CONFIDENCE_ESCALATION_THRESHOLD",
    "LocalisationHit",
    "TriageClient",
    "TriageContext",
    "classify_fix_class",
    "localise",
]
