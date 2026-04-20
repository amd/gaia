# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Tests for :mod:`gaia.coder.subagents.codebase_research` (§5.10).

The real engine (Claude Opus wrapping File/Search tools) is never invoked.
Every test supplies a stub engine that returns a canned
:class:`StructuredAnalysis` after N steps. ``cloner`` is stubbed so no
real ``git clone`` happens.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from gaia.coder.subagents.codebase_research import (
    BudgetExceededError,
    CodebaseResearchError,
    KeyFile,
    Pattern,
    ResearchBudget,
    StructuredAnalysis,
    research,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _fake_repo(tmp_path: Path) -> Path:
    """Minimal 'local repo' layout we can point ``source=`` at."""
    root = tmp_path / "fake_repo"
    (root / "src").mkdir(parents=True)
    (root / "src" / "hello.py").write_text("def hi():\n    return 'hi'\n")
    (root / "LICENSE").write_text("MIT License\n")
    return root


class _FixedEngine:
    """Engine that returns its canned analysis after ``turns_until_done`` steps."""

    def __init__(
        self,
        analysis: StructuredAnalysis,
        *,
        turns_until_done: int = 1,
        per_step_usd: float = 0.1,
        per_step_tokens: int = 500,
    ) -> None:
        self.analysis = analysis
        self.turns_until_done = turns_until_done
        self.per_step_usd = per_step_usd
        self.per_step_tokens = per_step_tokens
        self.calls = 0
        self.seen_workdirs: list[Path] = []

    def step(self, workdir, question, budget):
        self.calls += 1
        self.seen_workdirs.append(workdir)
        budget.tool_calls_made += 1
        budget.usd_spent += self.per_step_usd
        budget.tokens_used += self.per_step_tokens
        if self.calls >= self.turns_until_done:
            return self.analysis
        return None


class _RunawayEngine:
    """Engine that *never* returns a final answer (exercises budget)."""

    def __init__(self, per_step_usd: float = 0.5) -> None:
        self.per_step_usd = per_step_usd
        self.calls = 0

    def step(self, workdir, question, budget):
        self.calls += 1
        budget.tool_calls_made += 1
        budget.usd_spent += self.per_step_usd
        budget.tokens_used += 1000
        return None


def _stub_cloner():
    """Cloner that just creates an empty directory (stands in for a real clone)."""

    def _clone(url, dest):
        dest.mkdir(parents=True)
        (dest / "FROM_URL").write_text(url)

    return _clone


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------


def test_structured_analysis_schema_fields():
    """§5.10 schema — every required field is present."""
    a = StructuredAnalysis(
        source="https://github.com/foo/bar",
        question="Q",
        answer="A",
        key_files=[KeyFile(path="x.py", line_range=(1, 10), why_cited="y")],
        patterns_worth_adopting=[Pattern(pattern="p", evidence_path="e", caveat="c")],
        license="MIT",
        attribution_note="n",
        confidence=80,
        tokens_used=1000,
        usd_spent=0.5,
        unresolved_questions=["u"],
    )
    assert a.confidence == 80
    assert a.key_files[0].line_range == (1, 10)


# ---------------------------------------------------------------------------
# research — engine injection is required
# ---------------------------------------------------------------------------


def test_research_without_engine_raises(tmp_path):
    with pytest.raises(CodebaseResearchError, match="no research engine"):
        research(
            source=str(_fake_repo(tmp_path)),
            question="q",
            scratch_root=tmp_path / "scratch",
        )


# ---------------------------------------------------------------------------
# Happy path — local source
# ---------------------------------------------------------------------------


def test_research_happy_path_local(tmp_path):
    repo = _fake_repo(tmp_path)
    canned = StructuredAnalysis(
        source="<will-be-overwritten>",
        question="<will-be-overwritten>",
        answer="they use a sandbox-dispatch pattern",
        license="MIT",
        attribution_note="cite as github.com/fake/repo",
        confidence=82,
    )
    engine = _FixedEngine(canned, turns_until_done=2)
    result = research(
        source=str(repo),
        question="What pattern do they use?",
        engine=engine,
        scratch_root=tmp_path / "scratch",
    )
    assert isinstance(result, StructuredAnalysis)
    assert result.answer == "they use a sandbox-dispatch pattern"
    # The runner overrides source/question so the echoed slots always match input.
    assert result.source == str(repo)
    assert result.question == "What pattern do they use?"
    # Budget metrics folded back into the output.
    assert result.usd_spent == pytest.approx(0.2, abs=1e-6)
    assert result.tokens_used == 1000
    # The engine saw the workdir (a COPY of repo, not the original).
    assert engine.seen_workdirs[0] != repo


def test_research_cleans_up_scratch_by_default(tmp_path):
    repo = _fake_repo(tmp_path)
    engine = _FixedEngine(
        StructuredAnalysis(source="x", question="q", answer="a", confidence=50)
    )
    scratch = tmp_path / "scratch"
    research(
        source=str(repo),
        question="q",
        engine=engine,
        scratch_root=scratch,
    )
    # The scratch ROOT still exists (we don't delete that), but no child
    # workspaces remain.
    assert scratch.exists()
    assert list(scratch.iterdir()) == []


def test_research_keep_flag_preserves_workspace(tmp_path):
    repo = _fake_repo(tmp_path)
    engine = _FixedEngine(
        StructuredAnalysis(source="x", question="q", answer="a", confidence=50)
    )
    scratch = tmp_path / "scratch"
    research(
        source=str(repo),
        question="q",
        engine=engine,
        scratch_root=scratch,
        keep=True,
    )
    children = list(scratch.iterdir())
    assert len(children) == 1  # workspace retained
    assert (children[0] / "src" / "hello.py").exists()


# ---------------------------------------------------------------------------
# Happy path — remote URL via stub cloner
# ---------------------------------------------------------------------------


def test_research_remote_url_uses_cloner(tmp_path):
    engine = _FixedEngine(
        StructuredAnalysis(source="x", question="q", answer="from clone", confidence=70)
    )
    scratch = tmp_path / "scratch"
    result = research(
        source="https://github.com/fake/repo",
        question="anything",
        engine=engine,
        scratch_root=scratch,
        cloner=_stub_cloner(),
    )
    assert result.answer == "from clone"


def test_research_cloner_failure_raises(tmp_path):
    engine = _FixedEngine(
        StructuredAnalysis(source="x", question="q", answer="a", confidence=0)
    )

    def _broken_cloner(url, dest):
        raise CodebaseResearchError(f"could not clone {url}")

    with pytest.raises(CodebaseResearchError, match="could not clone"):
        research(
            source="https://github.com/broken/repo",
            question="q",
            engine=engine,
            scratch_root=tmp_path / "scratch",
            cloner=_broken_cloner,
        )


# ---------------------------------------------------------------------------
# Budget enforcement (§5.10)
# ---------------------------------------------------------------------------


def test_research_enforces_dollar_budget(tmp_path):
    repo = _fake_repo(tmp_path)
    engine = _RunawayEngine(per_step_usd=1.0)
    with pytest.raises(BudgetExceededError) as exc:
        research(
            source=str(repo),
            question="q",
            engine=engine,
            max_cost_usd=2.0,
            max_tool_calls=100,
            scratch_root=tmp_path / "scratch",
        )
    assert "dollar budget" in exc.value.reason
    # Partial is attached for logging.
    assert exc.value.partial is not None
    assert exc.value.partial.usd_spent > 2.0


def test_research_enforces_tool_call_budget(tmp_path):
    repo = _fake_repo(tmp_path)
    engine = _RunawayEngine(per_step_usd=0.01)
    with pytest.raises(BudgetExceededError) as exc:
        research(
            source=str(repo),
            question="q",
            engine=engine,
            max_cost_usd=100.0,
            max_tool_calls=3,
            scratch_root=tmp_path / "scratch",
        )
    assert "tool-call budget" in exc.value.reason


def test_research_enforces_wallclock_budget(tmp_path, monkeypatch):
    """Advance monotonic time by large jumps to trip the wall-clock ceiling."""
    repo = _fake_repo(tmp_path)
    engine = _RunawayEngine(per_step_usd=0.01)

    # Fake monotonic: advance 10 minutes per call after the first.
    fake_now = [1000.0]
    calls = [0]

    def _fake_monotonic():
        calls[0] += 1
        if calls[0] > 1:
            fake_now[0] += 700.0  # ~11.6 minutes per tick
        return fake_now[0]

    import gaia.coder.subagents.codebase_research as mod

    monkeypatch.setattr(mod.time, "monotonic", _fake_monotonic)
    with pytest.raises(BudgetExceededError) as exc:
        research(
            source=str(repo),
            question="q",
            engine=engine,
            max_duration_minutes=10.0,
            max_cost_usd=1000.0,
            max_tool_calls=1000,
            scratch_root=tmp_path / "scratch",
        )
    assert "wall-clock" in exc.value.reason


def test_research_cleans_up_even_on_budget_exception(tmp_path):
    repo = _fake_repo(tmp_path)
    engine = _RunawayEngine(per_step_usd=1.0)
    scratch = tmp_path / "scratch"
    with pytest.raises(BudgetExceededError):
        research(
            source=str(repo),
            question="q",
            engine=engine,
            max_cost_usd=2.0,
            scratch_root=scratch,
        )
    # Cleanup still happens in the `finally` block.
    assert scratch.exists()
    assert list(scratch.iterdir()) == []


# ---------------------------------------------------------------------------
# ResearchBudget primitives
# ---------------------------------------------------------------------------


def test_research_budget_not_exceeded_initially():
    b = ResearchBudget(max_duration_minutes=10, max_cost_usd=2.0)
    assert b.exceeded() is None


def test_research_budget_exceeded_by_dollars():
    b = ResearchBudget(max_duration_minutes=10, max_cost_usd=2.0)
    b.usd_spent = 3.0
    reason = b.exceeded()
    assert reason and "dollar" in reason


def test_research_budget_exceeded_by_tool_calls():
    b = ResearchBudget(max_tool_calls=5)
    b.tool_calls_made = 5
    reason = b.exceeded()
    assert reason and "tool-call" in reason


# ---------------------------------------------------------------------------
# Local-source validation
# ---------------------------------------------------------------------------


def test_research_missing_local_path_raises(tmp_path):
    engine = _FixedEngine(
        StructuredAnalysis(source="x", question="q", answer="a", confidence=0)
    )
    with pytest.raises(CodebaseResearchError, match="does not exist"):
        research(
            source="/does/not/exist/abc123",
            question="q",
            engine=engine,
            scratch_root=tmp_path / "scratch",
        )
