# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Contract for ``packaging/assert_llm_coverage.py``.

EmailTriageAgent's heuristic path classifies without calling the model and, on
that path, omits ``llm_classified_count`` from the scorecard entirely rather
than writing 0. A rules-only run therefore yields a complete, green, meaningless
scorecard. This gate must treat ABSENT and ZERO as equally fatal.

Fixtures are built through the REAL ``gaia.eval.scorecard.build_scorecard`` plus
the ``llm_classified_count`` merge the harness performs at ``benchmark.py:639``,
not a hand-written dict. A hand-rolled payload cannot catch the denominator
moving -- an earlier draft of this gate read ``performance.emails_per_run`` and
``test_cases_run``, neither of which exists in ``scorecard.json`` (both are
release-card fields added later by ``gen_scorecard.py``).

``packaging/`` has no ``__init__.py`` (mirrors ``server.py``/``stamp_version.py``)
so the module is loaded by file path, exactly like ``test_capability_matrix.py``.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

from gaia.eval.scorecard import build_scorecard

_EMAIL_ROOT = Path(__file__).resolve().parents[1]
_MODULE_PATH = _EMAIL_ROOT / "packaging" / "assert_llm_coverage.py"

_spec = importlib.util.spec_from_file_location("assert_llm_coverage", _MODULE_PATH)
assert_llm_coverage = importlib.util.module_from_spec(_spec)
sys.modules["assert_llm_coverage"] = assert_llm_coverage
_spec.loader.exec_module(assert_llm_coverage)


def _write_scorecard(
    tmp_path: Path,
    *,
    total_emails: int | None = 250,
    classified: float | None = 250.0,
    runs: int = 1,
) -> Path:
    """Write an eval-out/scorecard.json the way the harness actually writes it.

    ``classified=None`` reproduces the heuristic-only path (key absent, because
    no LLM call means no usage block to aggregate).
    """
    results = []
    for _ in range(runs):
        ps: dict = {
            "avg_tokens_per_second": 23.7,
            "avg_time_to_first_token": 24.6,
            "total_input_tokens": 100,
            "total_output_tokens": 50,
        }
        if total_emails is not None:
            ps["total_emails"] = total_emails
        results.append(
            {
                "status": "PASS",
                "overall_score": 8.0,
                "category": "triage",
                "performance_summary": ps,
            }
        )
    scorecard = build_scorecard("run1", results, {"model": "Gemma-4-E4B-it-GGUF"})
    # benchmark.py:639 merges the aggregated triage usage into performance.
    if classified is not None:
        scorecard["performance"]["llm_classified_count"] = classified
    (tmp_path / "scorecard.json").write_text(json.dumps(scorecard), encoding="utf-8")
    return tmp_path


class TestLlmCoverageGate:
    def test_healthy_coverage_passes(self, tmp_path):
        assert assert_llm_coverage.check(_write_scorecard(tmp_path)) == 0

    def test_absent_count_fails(self, tmp_path):
        # The heuristic-only path: key omitted, NOT zeroed. This is the real
        # shape of the bug and the one a `== 0` check would miss.
        d = _write_scorecard(tmp_path, total_emails=20, classified=None)
        assert assert_llm_coverage.check(d) == 1

    def test_zero_count_fails(self, tmp_path):
        d = _write_scorecard(tmp_path, total_emails=20, classified=0)
        assert assert_llm_coverage.check(d) == 1

    def test_partial_coverage_passes(self, tmp_path):
        # A legitimate mixed run (some heuristic, some LLM) is not a failure —
        # the gate asserts the model was reached, not that it handled everything.
        d = _write_scorecard(tmp_path, total_emails=20, classified=12.0)
        assert assert_llm_coverage.check(d) == 0

    def test_missing_scorecard_file_fails(self, tmp_path):
        assert assert_llm_coverage.check(tmp_path) == 1

    def test_corrupt_scorecard_fails_without_traceback(self, tmp_path):
        (tmp_path / "scorecard.json").write_text("{not json", encoding="utf-8")
        assert assert_llm_coverage.check(tmp_path) == 1


class TestDenominator:
    """The denominator must come from scenarios[].performance_summary.total_emails
    — the harness's scorecard.json has no emails_per_run/test_cases_run."""

    def test_corpus_size_read_from_scenarios(self, tmp_path):
        payload = json.loads(
            (_write_scorecard(tmp_path, total_emails=250) / "scorecard.json").read_text(
                encoding="utf-8"
            )
        )
        assert assert_llm_coverage._corpus_size(payload) == 250

    def test_release_card_keys_are_absent_from_harness_output(self, tmp_path):
        # Pins the shape mistake this gate previously made. If the harness ever
        # starts emitting these, revisit — but do not read them today.
        payload = json.loads(
            (_write_scorecard(tmp_path) / "scorecard.json").read_text(encoding="utf-8")
        )
        assert "emails_per_run" not in payload["performance"]
        assert "test_cases_run" not in payload

    def test_percentage_reported_when_denominator_present(self, tmp_path, capsys):
        assert_llm_coverage.check(_write_scorecard(tmp_path, total_emails=250))
        # count is a cross-run mean, hence a float even at experiments=1.
        assert "/ 250 messages (100.0%)" in capsys.readouterr().out

    def test_missing_denominator_still_passes_on_positive_count(self, tmp_path):
        # Degrade gracefully: coverage is proven, only the ratio is unknown.
        d = _write_scorecard(tmp_path, total_emails=None, classified=250.0)
        assert assert_llm_coverage.check(d) == 0


class TestJobSummary:
    @pytest.mark.parametrize(
        "classified,marker", [(250.0, "✅"), (0, "❌"), (None, "❌")]
    )
    def test_verdict_written_to_job_summary(
        self, tmp_path, monkeypatch, classified, marker
    ):
        summary = tmp_path / "summary.md"
        monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
        assert_llm_coverage.check(_write_scorecard(tmp_path, classified=classified))
        body = summary.read_text(encoding="utf-8")
        assert "LLM coverage" in body and marker in body

    def test_main_accepts_dir_argument(self, tmp_path):
        assert assert_llm_coverage.main([str(_write_scorecard(tmp_path))]) == 0
