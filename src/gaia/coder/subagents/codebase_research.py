# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""``codebase_research`` — the §5.10 subagent that investigates external repos.

The main ``gaia-coder`` loop stays bound to ``amd/gaia``; external code (a
library we're integrating, a fork we're evaluating) needs understanding
without *polluting* the primary RAG. :func:`research` does exactly that:

1. Clones (remote) or opens (local) the source into a scratch workspace
   under ``~/.gaia/coder/research/<session-id>/`` by default.
2. Loads a **minimal tool set** — :class:`FileToolsMixin` +
   :class:`SearchToolsMixin`. Explicitly *no* writes, *no* GitHub CLI, *no*
   memory writes. The subagent is purely observational.
3. Answers the question in at most ``max_tool_calls`` tool calls, under a
   ``max_duration_minutes`` wall-clock cap and a ``max_cost_usd`` ceiling.
4. Returns a :class:`StructuredAnalysis` — the §5.10 schema verbatim.
5. Cleans up the scratch workspace on exit unless ``keep=True``.

Budget is enforced at two layers:

* **The runner** loops tool calls and checks ``budget.exceeded(...)``
  between each. Exceeding the budget raises :class:`BudgetExceededError`.
* **The engine** (passed in by the caller) is responsible for advancing
  ``usd_spent`` and ``tokens_used`` — the runner is engine-agnostic.

No Anthropic / Claude SDK calls happen inside this module. A caller
supplies an ``engine`` callable; tests pass a stub that returns canned
answers. This matches the "mock the subprocess / LLM boundary" discipline
the other phases use.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, List, Optional, Protocol, Tuple

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Budget
# ---------------------------------------------------------------------------


@dataclass
class ResearchBudget:
    """Wall-clock + dollar ceiling for a single :func:`research` call.

    Defaults match §5.10 (``max_duration_minutes=10``, ``max_cost_usd=2.0``).
    The runner asks :meth:`exceeded` between every tool call. ``tokens_used``
    and ``usd_spent`` are advanced by the engine; the runner just reads.
    """

    # Lambda (not bare ``time.monotonic``) so tests that monkeypatch
    # ``time.monotonic`` on the module also affect ``started_at``.
    started_at: float = field(
        default_factory=lambda: time.monotonic()  # pylint: disable=unnecessary-lambda
    )
    max_duration_minutes: float = 10.0
    max_cost_usd: float = 2.0
    max_tool_calls: int = 40
    usd_spent: float = 0.0
    tokens_used: int = 0
    tool_calls_made: int = 0

    def elapsed_s(self) -> float:
        return time.monotonic() - self.started_at

    def exceeded(self) -> Optional[str]:
        """Return a human-readable reason or ``None`` if under budget."""
        if self.elapsed_s() > self.max_duration_minutes * 60:
            return (
                f"wall-clock budget exceeded: "
                f"{self.elapsed_s() / 60:.1f}m > {self.max_duration_minutes}m"
            )
        if self.usd_spent > self.max_cost_usd:
            return (
                f"dollar budget exceeded: "
                f"${self.usd_spent:.2f} > ${self.max_cost_usd:.2f}"
            )
        if self.tool_calls_made >= self.max_tool_calls:
            return (
                f"tool-call budget exceeded: "
                f"{self.tool_calls_made} >= {self.max_tool_calls}"
            )
        return None


# ---------------------------------------------------------------------------
# Structured output — §5.10 schema verbatim
# ---------------------------------------------------------------------------


class KeyFile(BaseModel):
    """One row in :attr:`StructuredAnalysis.key_files`."""

    path: str
    line_range: Tuple[int, int]
    why_cited: str


class Pattern(BaseModel):
    """One row in :attr:`StructuredAnalysis.patterns_worth_adopting`."""

    pattern: str
    evidence_path: str
    caveat: str


class StructuredAnalysis(BaseModel):
    """The §5.10 schema — fields exactly as specified."""

    source: str
    question: str
    answer: str
    key_files: List[KeyFile] = Field(default_factory=list)
    patterns_worth_adopting: List[Pattern] = Field(default_factory=list)
    license: Optional[str] = None
    attribution_note: str = ""
    confidence: int = 0  # 0-100
    tokens_used: int = 0
    usd_spent: float = 0.0
    unresolved_questions: List[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class CodebaseResearchError(RuntimeError):
    """Raised when :func:`research` cannot start or cannot return a result."""


class BudgetExceededError(CodebaseResearchError):
    """Raised when the budget ceiling is hit mid-run.

    The partial :class:`StructuredAnalysis` (if any) is attached via the
    ``partial`` attribute so callers can still log what they got before
    the ceiling fired.
    """

    def __init__(
        self, reason: str, partial: Optional[StructuredAnalysis] = None
    ) -> None:
        self.reason = reason
        self.partial = partial
        super().__init__(reason)


# ---------------------------------------------------------------------------
# Source adapter — local path vs remote URL
# ---------------------------------------------------------------------------


def _is_remote_url(source: str) -> bool:
    """True iff ``source`` looks like a git-cloneable URL."""
    return (
        source.startswith("https://")
        or source.startswith("git@")
        or source.startswith("git://")
        or source.startswith("ssh://")
    )


def _prepare_workspace(
    source: str,
    scratch_root: Path,
    *,
    cloner: Callable[[str, Path], None],
) -> Path:
    """Materialise ``source`` into a subdirectory under ``scratch_root``.

    Returns the absolute path to the working copy. Raises on cloning
    failure.
    """
    scratch_root.mkdir(parents=True, exist_ok=True)
    slug = uuid.uuid4().hex[:8]
    workdir = scratch_root / slug
    if _is_remote_url(source):
        cloner(source, workdir)
    else:
        src_path = Path(source).expanduser().resolve()
        if not src_path.exists():
            raise CodebaseResearchError(f"local source path does not exist: {src_path}")
        # Copy rather than symlink — subagent must not accidentally write
        # back to the real repo.
        shutil.copytree(src_path, workdir)
    return workdir


def _default_cloner(url: str, dest: Path) -> None:
    """Invoke ``git clone --depth 1 URL DEST`` via :mod:`subprocess`."""
    # --depth 1 makes the clone fast; we don't need history for static
    # analysis. Callers who want full history pass their own cloner.
    completed = subprocess.run(
        ["git", "clone", "--depth", "1", url, str(dest)],
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )
    if completed.returncode != 0:
        raise CodebaseResearchError(
            f"git clone {url!r} failed ({completed.returncode}): "
            f"{completed.stderr.strip()}"
        )


# ---------------------------------------------------------------------------
# Engine protocol
# ---------------------------------------------------------------------------


class ResearchEngine(Protocol):
    """Abstract interface the runner uses to drive a single research session.

    Real implementations wrap a Claude / Opus call with the minimal tool
    set (FileTools + SearchTools) bound to ``workdir``. Tests pass a stub
    that returns a canned :class:`StructuredAnalysis`.

    The engine is expected to mutate ``budget.tokens_used`` /
    ``budget.usd_spent`` / ``budget.tool_calls_made`` as it goes so the
    runner can enforce the ceiling between turns.
    """

    def step(
        self, workdir: Path, question: str, budget: ResearchBudget
    ) -> Optional[StructuredAnalysis]:
        """Advance one reasoning turn.

        Return ``None`` to signal "more turns needed" — the runner will
        loop. Return a :class:`StructuredAnalysis` to finish.
        """
        ...


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


DEFAULT_SCRATCH_ROOT = Path.home() / ".gaia" / "coder" / "research"


def research(
    source: str,
    question: str,
    *,
    engine: Optional[ResearchEngine] = None,
    max_duration_minutes: float = 10.0,
    max_cost_usd: float = 2.0,
    max_tool_calls: int = 40,
    scratch_root: Optional[Path] = None,
    cloner: Optional[Callable[[str, Path], None]] = None,
    keep: bool = False,
) -> StructuredAnalysis:
    """Dispatch the §5.10 codebase-research subagent.

    Args:
        source: Either a git URL (``https://…``/``git@…``) or a local
            repo path.
        question: Free-text question the subagent answers.
        engine: Research engine implementing :class:`ResearchEngine`. If
            ``None``, raises — there is no default engine in this module
            (the LLM binding lives in the caller).
        max_duration_minutes: §5.10 default 10.
        max_cost_usd: §5.10 default $2.
        max_tool_calls: Upper bound on tool invocations. Protects against
            a runaway engine that never emits a final answer.
        scratch_root: Override ``~/.gaia/coder/research/``. Tests pass
            ``tmp_path``.
        cloner: Injected clone runner. Tests stub this to avoid touching
            the network.
        keep: If True, leave the scratch workspace behind for inspection.
            Default False — clean up on exit.

    Raises:
        CodebaseResearchError: Source prep fails, or the engine returns
            no final answer under the ceiling.
        BudgetExceededError: The budget is tripped mid-run.
    """
    if engine is None:
        raise CodebaseResearchError(
            "no research engine supplied; pass `engine=` with a "
            "ResearchEngine implementation"
        )
    root = scratch_root or DEFAULT_SCRATCH_ROOT
    use_cloner = cloner or _default_cloner
    workdir = _prepare_workspace(source, root, cloner=use_cloner)
    logger.info(
        "codebase_research: starting (source=%s workdir=%s budget=%.1fm/$%.2f)",
        source,
        workdir,
        max_duration_minutes,
        max_cost_usd,
    )
    budget = ResearchBudget(
        max_duration_minutes=max_duration_minutes,
        max_cost_usd=max_cost_usd,
        max_tool_calls=max_tool_calls,
    )
    last_partial: Optional[StructuredAnalysis] = None
    try:
        while True:
            reason = budget.exceeded()
            if reason is not None:
                raise BudgetExceededError(reason, partial=last_partial)
            result = engine.step(workdir, question, budget)
            if result is not None:
                # Copy final-state budget metrics into the structured output so
                # the caller sees what the run actually cost.
                result.tokens_used = budget.tokens_used
                result.usd_spent = round(budget.usd_spent, 4)
                result.source = source
                result.question = question
                return result
            last_partial = _maybe_partial(budget, source, question)
    finally:
        if not keep:
            shutil.rmtree(workdir, ignore_errors=True)


def _maybe_partial(
    budget: ResearchBudget, source: str, question: str
) -> Optional[StructuredAnalysis]:
    """Return a synthetic partial :class:`StructuredAnalysis` for budget exceptions.

    Used only to preserve budget metrics on a failed run. The real engine
    would populate substantive fields; this helper fills in what we know
    cheaply so the caller has something to log.
    """
    return StructuredAnalysis(
        source=source,
        question=question,
        answer="<budget exceeded before engine returned a final answer>",
        confidence=0,
        tokens_used=budget.tokens_used,
        usd_spent=round(budget.usd_spent, 4),
    )


__all__ = [
    "BudgetExceededError",
    "CodebaseResearchError",
    "DEFAULT_SCRATCH_ROOT",
    "KeyFile",
    "Pattern",
    "ResearchBudget",
    "ResearchEngine",
    "StructuredAnalysis",
    "research",
]
