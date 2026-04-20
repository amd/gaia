# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Unit tests for ``gaia.coder.self_fix.triage``."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from gaia.coder.self_fix.triage import (
    FIX_CLASSES,
    CandidateFile,
    TriageContext,
    classify_fix_class,
    localise,
)


def _make_client(payload: dict):
    """Factory: a mock TriageClient that returns ``payload`` as JSON text."""

    def client(**_kwargs):
        return json.dumps(payload)

    return client


@pytest.mark.parametrize("fix_class", list(FIX_CLASSES))
def test_triage_classifies_all_fix_classes(fix_class: str) -> None:
    """All eight fix-class labels from §7.4 step 1 must round-trip through the parser."""
    payload = {
        "fix_class": fix_class,
        "root_cause_hypothesis": f"dummy hypothesis for {fix_class}",
        "candidate_files": [
            {"path": "src/gaia/coder/sample.py:1-10", "why": "touches the thing"}
        ],
        "prior_pattern_hit": None,
        # High confidence so no low-confidence escalation masks the label.
        "confidence": 90,
    }
    ctx = TriageContext(
        feedback_id="fb1",
        received_at="2026-04-20T00:00:00+00:00",
        from_handle="em",
        severity="high",
    )
    result = classify_fix_class("some feedback body", ctx, client=_make_client(payload))
    assert result.fix_class == fix_class
    assert result.confidence == 90
    assert result.escalated_low_confidence is False
    assert result.candidate_files[0].path == "src/gaia/coder/sample.py:1-10"


def test_triage_conservative_on_low_confidence() -> None:
    """§7.2 confidence rule: < 60 rewrites fix_class to out-of-scope."""
    payload = {
        "fix_class": "tool",
        "root_cause_hypothesis": "uncertain guess",
        "candidate_files": [],
        "prior_pattern_hit": None,
        "confidence": 42,
    }
    ctx = TriageContext(
        feedback_id="fb-low",
        received_at="2026-04-20T00:00:00+00:00",
        from_handle="em",
        severity="med",
    )
    result = classify_fix_class("??", ctx, client=_make_client(payload))
    assert result.fix_class == "out-of-scope"
    assert result.escalated_low_confidence is True
    assert result.confidence == 42


def test_triage_rejects_unknown_fix_class() -> None:
    """An unknown fix_class in the LLM response must fail loudly."""
    payload = {
        "fix_class": "made-up",
        "root_cause_hypothesis": "x",
        "candidate_files": [],
        "prior_pattern_hit": None,
        "confidence": 95,
    }
    ctx = TriageContext(
        feedback_id="fb-bad",
        received_at="2026-04-20T00:00:00+00:00",
        from_handle="em",
        severity="low",
    )
    with pytest.raises(ValueError, match="unknown fix_class"):
        classify_fix_class("x", ctx, client=_make_client(payload))


def test_localise_finds_candidates(tmp_path: Path) -> None:
    """Deterministic grep: keywords from the feedback must land concrete hits."""
    repo = tmp_path / "repo"
    target = repo / "src" / "mod.py"
    target.parent.mkdir(parents=True)
    target.write_text(
        "def classify_failure():\n"
        "    pass\n"
        "\n"
        "def other():\n"
        "    # note: cache collision on timestamped errors\n"
        "    pass\n",
        encoding="utf-8",
    )
    candidates = (CandidateFile(path="src/mod.py", why="seeded"),)
    hits = localise(
        "tool",
        candidates,
        repo_root=repo,
        keywords=["timestamped", "classify_failure"],
    )
    assert hits, "expected at least one localisation hit"
    paths = {h.path for h in hits}
    assert "src/mod.py" in paths
    # One of the hits must mention the keyword.
    assert any(
        "timestamped" in h.snippet or "classify_failure" in h.snippet for h in hits
    )


def test_localise_range_extracts_lines(tmp_path: Path) -> None:
    """A ``file:start-end`` spec returns the exact line range."""
    repo = tmp_path / "repo2"
    (repo / "src").mkdir(parents=True)
    target = repo / "src" / "mod.py"
    target.write_text("\n".join(f"line {i}" for i in range(1, 21)), encoding="utf-8")

    candidates = (CandidateFile(path="src/mod.py:5-7", why="range test"),)
    hits = localise("tool", candidates, repo_root=repo)
    assert len(hits) == 1
    assert hits[0].line_start == 5
    assert hits[0].line_end == 7
    assert hits[0].snippet.splitlines() == ["line 5", "line 6", "line 7"]


def test_localise_skips_missing_files(tmp_path: Path) -> None:
    """A candidate pointing at a nonexistent path is skipped, not crashed."""
    candidates = (CandidateFile(path="does/not/exist.py", why="nope"),)
    hits = localise("tool", candidates, repo_root=tmp_path)
    assert hits == []
