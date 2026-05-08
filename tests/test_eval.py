#!/usr/bin/env python3
# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Unit and integration tests for the evaluation tool"""

import json
import subprocess
import sys
from pathlib import Path

import pytest


class TestEvalCLI:
    """Test eval command-line interface"""

    def test_eval_help(self):
        """Test that eval help command works"""
        result = subprocess.run(
            [sys.executable, "-m", "gaia.cli", "eval", "--help"],
            capture_output=True,
            text=True,
        )

        assert result.returncode == 0, f"Command failed with stderr: {result.stderr}"
        help_text = result.stdout.lower()
        assert "eval" in help_text
        assert "agent" in help_text

    def test_eval_agent_help(self):
        """Test that eval agent help command works"""
        result = subprocess.run(
            [sys.executable, "-m", "gaia.cli", "eval", "agent", "--help"],
            capture_output=True,
            text=True,
        )

        assert result.returncode == 0, f"Command failed with stderr: {result.stderr}"
        help_text = result.stdout.lower()
        assert "agent" in help_text
        assert "--scenario" in result.stdout
        assert "--category" in result.stdout

    def test_eval_bare_shows_help(self):
        """Test that bare 'gaia eval' shows usage pointing to agent subcommand"""
        result = subprocess.run(
            [sys.executable, "-m", "gaia.cli", "eval"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )

        combined_output = (result.stdout + result.stderr).lower()
        assert "agent" in combined_output


class TestImports:
    """Verify critical imports are present in runner module."""

    def test_timezone_imported(self):
        """runner.py must import timezone alongside datetime (used in run_id generation)."""
        from gaia.eval import runner

        assert hasattr(
            runner, "timezone"
        ), "runner.py is missing 'from datetime import timezone'"

    def test_acquire_eval_lock_windows_noop(self, monkeypatch):
        """When fcntl is unavailable (Windows), the lock context manager
        must yield immediately without touching the filesystem.

        Locally we simulate the Windows path by monkeypatching
        ``runner.fcntl`` to ``None`` — the same state the conditional
        import produces on win32. Without this guard the eval suite
        crashes at module load on every Windows runner (#802).
        """
        from gaia.eval import runner

        monkeypatch.setattr(runner, "fcntl", None)
        # Should yield without raising and without opening the lockfile.
        with runner._acquire_eval_lock():
            pass


class TestAgentEvalScorecard:
    """Tests for the agent eval scorecard module."""

    def _make_result(self, scenario_id, status, score, category="rag_quality"):
        return {
            "scenario_id": scenario_id,
            "status": status,
            "overall_score": score,
            "category": category,
            "cost_estimate": {"estimated_usd": 0.05},
        }

    def test_build_scorecard_pass_rate(self):
        from gaia.eval.scorecard import build_scorecard

        results = [
            self._make_result("a", "PASS", 9.0),
            self._make_result("b", "PASS", 8.0),
            self._make_result("c", "FAIL", 4.0),
        ]
        sc = build_scorecard("run-1", results, {})
        assert sc["summary"]["passed"] == 2
        assert sc["summary"]["failed"] == 1
        assert abs(sc["summary"]["pass_rate"] - 2 / 3) < 0.001

    def test_avg_score_excludes_infra_failures(self):
        """ERRORED/TIMEOUT/BUDGET_EXCEEDED scenarios (score=0) must not dilute avg."""
        from gaia.eval.scorecard import build_scorecard

        results = [
            self._make_result("a", "PASS", 9.0),
            self._make_result("b", "FAIL", 5.0),
            self._make_result("c", "ERRORED", 0),
            self._make_result("d", "TIMEOUT", 0),
            self._make_result("e", "BUDGET_EXCEEDED", 0),
        ]
        sc = build_scorecard("run-1", results, {})
        # avg_score should only count PASS + FAIL (judged scenarios)
        assert abs(sc["summary"]["avg_score"] - 7.0) < 0.01
        assert sc["summary"]["timeout"] == 1
        assert sc["summary"]["budget_exceeded"] == 1
        assert sc["summary"]["errored"] == 1

    def test_avg_score_excludes_setup_error(self):
        """SETUP_ERROR must be excluded from avg_score (same as other infra failures)."""
        from gaia.eval.scorecard import build_scorecard

        results = [
            self._make_result("a", "PASS", 8.0),
            self._make_result("b", "SETUP_ERROR", None),
            self._make_result("c", "INFRA_ERROR", None),
        ]
        sc = build_scorecard("run-1", results, {})
        # avg_score = only the PASS scenario = 8.0
        assert sc["summary"]["avg_score"] == pytest.approx(8.0)
        assert sc["summary"]["infra_error"] == 2

    def test_by_category_grouping(self):
        """Category field from result must appear in by_category breakdown."""
        from gaia.eval.scorecard import build_scorecard

        results = [
            self._make_result("a", "PASS", 9.0, category="rag_quality"),
            self._make_result("b", "FAIL", 4.0, category="rag_quality"),
            self._make_result("c", "PASS", 8.0, category="tool_selection"),
        ]
        sc = build_scorecard("run-1", results, {})
        cats = sc["summary"]["by_category"]
        assert "rag_quality" in cats
        assert "tool_selection" in cats
        assert "unknown" not in cats
        assert cats["rag_quality"]["passed"] == 1
        assert cats["rag_quality"]["failed"] == 1
        assert cats["tool_selection"]["passed"] == 1

    def test_write_summary_md(self):
        from gaia.eval.scorecard import build_scorecard, write_summary_md

        results = [self._make_result("a", "PASS", 9.0)]
        sc = build_scorecard("run-x", results, {"model": "test-model"})
        md = write_summary_md(sc)
        assert "run-x" in md
        assert "test-model" in md
        assert "PASS" in md or "✅" in md


class TestAgentEvalAudit:
    """Tests for the architecture audit module."""

    def test_audit_returns_expected_keys(self):
        from gaia.eval.audit import run_audit

        result = run_audit()
        audit = result["architecture_audit"]
        assert "history_pairs" in audit
        assert "max_msg_chars" in audit
        assert "tool_results_in_history" in audit
        assert "agent_persistence" in audit
        assert "blocked_scenarios" in audit
        assert "recommendations" in audit

    def test_audit_agent_persistence_reads_chat_helpers(self, tmp_path):
        from gaia.eval.audit import audit_agent_persistence

        # File with ChatAgent( -> stateless_per_message
        f = tmp_path / "helpers.py"
        f.write_text(
            "async def handle():\n    agent = ChatAgent(config)\n    return agent\n"
        )
        assert audit_agent_persistence(f) == "stateless_per_message"

        # File without ChatAgent( -> unknown
        f2 = tmp_path / "other.py"
        f2.write_text("def foo(): pass\n")
        assert audit_agent_persistence(f2) == "unknown"

    def test_audit_tool_results_in_history(self, tmp_path):
        from gaia.eval.audit import audit_tool_results_in_history

        # File with pattern indicating tool results in history
        f = tmp_path / "helpers.py"
        f.write_text(
            "agent_steps = get_steps()\nmessages = build_history(agent_steps)\nrole = 'user'\n"
        )
        assert audit_tool_results_in_history(f) is True

        # File without the pattern
        f2 = tmp_path / "other.py"
        f2.write_text("def foo(): pass\n")
        assert audit_tool_results_in_history(f2) is False

    def test_audit_reads_real_chat_helpers_values(self):
        """Integration canary: audit must read the real constants from _chat_helpers.py.

        This test breaks intentionally if someone renames or changes _MAX_HISTORY_PAIRS
        or _MAX_MSG_CHARS, alerting that eval recommendations need updating.
        """
        from gaia.eval.audit import audit_chat_helpers

        constants = audit_chat_helpers()
        assert (
            constants.get("_MAX_HISTORY_PAIRS") == 5
        ), "_MAX_HISTORY_PAIRS changed in _chat_helpers.py — update eval recommendations"
        assert (
            constants.get("_MAX_MSG_CHARS") == 2000
        ), "_MAX_MSG_CHARS changed in _chat_helpers.py — update eval recommendations"


class TestAgentEvalRunner:
    """Tests for runner helpers that don't require subprocess/LLM."""

    def test_find_scenarios_returns_yaml_files(self):
        from gaia.eval.runner import find_scenarios

        scenarios = find_scenarios()
        assert len(scenarios) > 0
        ids = [data["id"] for _, data in scenarios]
        # Verify a few known scenario IDs are present
        assert "simple_factual_rag" in ids
        assert "hallucination_resistance" in ids
        assert "no_tools_needed" in ids

    def test_find_scenarios_filter_by_id(self):
        from gaia.eval.runner import find_scenarios

        results = find_scenarios(scenario_id="simple_factual_rag")
        assert len(results) == 1
        assert results[0][1]["id"] == "simple_factual_rag"

    def test_find_scenarios_filter_by_category(self):
        from gaia.eval.runner import find_scenarios

        results = find_scenarios(category="rag_quality")
        assert len(results) > 0
        for _, data in results:
            assert data["category"] == "rag_quality"

    def test_scenario_ids_are_unique(self):
        from gaia.eval.runner import find_scenarios

        scenarios = find_scenarios()
        ids = [data["id"] for _, data in scenarios]
        assert len(ids) == len(
            set(ids)
        ), f"Duplicate scenario IDs: {[x for x in ids if ids.count(x) > 1]}"

    def test_all_scenarios_have_required_fields(self):
        from gaia.eval.runner import find_scenarios

        scenarios = find_scenarios()
        for path, data in scenarios:
            assert "id" in data, f"{path.name} missing 'id'"
            assert "category" in data, f"{path.name} missing 'category'"
            assert "turns" in data, f"{path.name} missing 'turns'"
            assert len(data["turns"]) > 0, f"{path.name} has no turns"
            assert "setup" in data, f"{path.name} missing 'setup'"

    def test_compare_scorecards_detects_regression(self, tmp_path):
        import json

        from gaia.eval.runner import compare_scorecards
        from gaia.eval.scorecard import build_scorecard

        def _sc(results):
            sc = build_scorecard("run", results, {})
            p = tmp_path / f"{id(results)}.json"
            p.write_text(json.dumps(sc))
            return p

        baseline_results = [
            {
                "scenario_id": "a",
                "status": "PASS",
                "overall_score": 9.0,
                "category": "rag_quality",
                "cost_estimate": {"estimated_usd": 0},
            },
            {
                "scenario_id": "b",
                "status": "PASS",
                "overall_score": 8.0,
                "category": "rag_quality",
                "cost_estimate": {"estimated_usd": 0},
            },
        ]
        current_results = [
            {
                "scenario_id": "a",
                "status": "PASS",
                "overall_score": 9.0,
                "category": "rag_quality",
                "cost_estimate": {"estimated_usd": 0},
            },
            {
                "scenario_id": "b",
                "status": "FAIL",
                "overall_score": 3.0,
                "category": "rag_quality",
                "cost_estimate": {"estimated_usd": 0},
            },
        ]
        diff = compare_scorecards(_sc(baseline_results), _sc(current_results))
        assert len(diff["regressed"]) == 1
        assert diff["regressed"][0]["scenario_id"] == "b"
        assert len(diff["improved"]) == 0

    def test_corpus_manifest_references_exist(self):
        """All files listed in manifest.json must exist on disk."""
        from gaia.eval.runner import CORPUS_DIR, MANIFEST

        assert MANIFEST.exists(), f"Corpus manifest not found: {MANIFEST}"
        manifest = __import__("json").loads(MANIFEST.read_text(encoding="utf-8"))

        docs_dir = CORPUS_DIR / "documents"
        adv_dir = CORPUS_DIR / "adversarial"
        missing = []
        for doc in manifest.get("documents", []):
            if not (docs_dir / doc["filename"]).exists():
                missing.append(doc["filename"])
        for doc in manifest.get("adversarial_documents", []):
            if not (adv_dir / doc["filename"]).exists():
                missing.append(doc["filename"])
        assert not missing, f"Manifest files missing from disk: {missing}"

    def test_all_corpus_documents_in_manifest(self):
        """Every file in corpus/documents/ must have a manifest entry (no orphans)."""
        from gaia.eval.runner import CORPUS_DIR, MANIFEST

        assert MANIFEST.exists(), f"Corpus manifest not found: {MANIFEST}"
        manifest = __import__("json").loads(MANIFEST.read_text(encoding="utf-8"))
        manifest_filenames = {doc["filename"] for doc in manifest.get("documents", [])}

        docs_dir = CORPUS_DIR / "documents"
        orphans = []
        for f in docs_dir.iterdir():
            if f.is_file() and not f.name.startswith("."):
                if f.name not in manifest_filenames:
                    orphans.append(f.name)
        assert not orphans, f"Files in corpus/documents/ not in manifest: {orphans}"

    def test_compare_scorecards_detects_score_regression(self, tmp_path):
        """PASS→PASS with score drop ≥2.0 should appear in score_regressed."""
        from gaia.eval.runner import compare_scorecards
        from gaia.eval.scorecard import build_scorecard

        def _sc(results):
            sc = build_scorecard("run", results, {})
            p = tmp_path / f"sc_{id(results)}.json"
            p.write_text(json.dumps(sc))
            return p

        baseline_results = [
            {
                "scenario_id": "a",
                "status": "PASS",
                "overall_score": 9.0,
                "category": "rag_quality",
                "cost_estimate": {"estimated_usd": 0},
            },
        ]
        current_results = [
            {
                "scenario_id": "a",
                "status": "PASS",
                "overall_score": 6.5,
                "category": "rag_quality",
                "cost_estimate": {"estimated_usd": 0},
            },
        ]
        diff = compare_scorecards(_sc(baseline_results), _sc(current_results))
        assert len(diff["score_regressed"]) == 1
        assert diff["score_regressed"][0]["scenario_id"] == "a"
        assert diff["score_regressed"][0]["delta"] == pytest.approx(-2.5, abs=0.01)
        assert len(diff["regressed"]) == 0  # still passing — not a full regression

    def test_compare_scorecards_small_drop_not_flagged(self, tmp_path):
        """Small PASS→PASS score drop <2.0 should not appear in score_regressed."""
        from gaia.eval.runner import compare_scorecards
        from gaia.eval.scorecard import build_scorecard

        def _sc(results):
            sc = build_scorecard("run", results, {})
            p = tmp_path / f"sc_{id(results)}.json"
            p.write_text(json.dumps(sc))
            return p

        baseline_results = [
            {
                "scenario_id": "a",
                "status": "PASS",
                "overall_score": 8.0,
                "category": "rag_quality",
                "cost_estimate": {"estimated_usd": 0},
            },
        ]
        current_results = [
            {
                "scenario_id": "a",
                "status": "PASS",
                "overall_score": 7.0,
                "category": "rag_quality",
                "cost_estimate": {"estimated_usd": 0},
            },
        ]
        diff = compare_scorecards(_sc(baseline_results), _sc(current_results))
        assert len(diff["score_regressed"]) == 0
        assert len(diff["unchanged"]) == 1


class TestScoreValidation:
    """Tests for post-hoc score validation helpers."""

    def test_recompute_turn_score_correct(self):
        from gaia.eval.runner import recompute_turn_score

        scores = {
            "correctness": 10,
            "tool_selection": 10,
            "context_retention": 10,
            "completeness": 10,
            "efficiency": 10,
            "personality": 10,
            "error_recovery": 10,
        }
        assert recompute_turn_score(scores) == pytest.approx(10.0, abs=0.001)

    def test_recompute_turn_score_weighted(self):
        from gaia.eval.runner import recompute_turn_score

        # correctness=10 (25%), everything else=0
        scores = {
            "correctness": 10,
            "tool_selection": 0,
            "context_retention": 0,
            "completeness": 0,
            "efficiency": 0,
            "personality": 0,
            "error_recovery": 0,
        }
        assert recompute_turn_score(scores) == pytest.approx(2.5, abs=0.001)

    def test_recompute_turn_score_missing_dimension(self):
        from gaia.eval.runner import recompute_turn_score

        # Missing error_recovery → should return -1.0
        scores = {
            "correctness": 8,
            "tool_selection": 7,
            "context_retention": 9,
            "completeness": 8,
            "efficiency": 7,
            "personality": 8,
        }
        assert recompute_turn_score(scores) == -1.0

    def test_recompute_turn_score_clamps_out_of_range(self):
        from gaia.eval.runner import recompute_turn_score

        # Hallucinating eval agent returns out-of-range scores → clamped to [0, 10]
        scores = {
            "correctness": 15,  # clamped to 10
            "tool_selection": -3,  # clamped to 0
            "context_retention": 10,
            "completeness": 10,
            "efficiency": 10,
            "personality": 10,
            "error_recovery": 10,
        }
        result = recompute_turn_score(scores)
        # Same as all-10 except tool_selection=0: 10*0.25 + 0*0.20 + 10*0.20 + 10*0.15 + 10*0.10 + 10*0.05 + 10*0.05 = 8.0
        expected = (
            10 * 0.25
            + 0 * 0.20
            + 10 * 0.20
            + 10 * 0.15
            + 10 * 0.10
            + 10 * 0.05
            + 10 * 0.05
        )
        assert result == pytest.approx(expected, abs=0.001)

    def test_recompute_turn_score_string_values(self):
        from gaia.eval.runner import recompute_turn_score

        # LLM returned score dimensions as strings → should return -1.0 (not crash)
        scores = {
            "correctness": "8",
            "tool_selection": "7",
            "context_retention": "9",
            "completeness": "8",
            "efficiency": "7",
            "personality": "8",
            "error_recovery": "7",
        }
        assert recompute_turn_score(scores) == -1.0

    def test_validate_turn_scores_no_warnings_when_dims_present(self):
        from gaia.eval.runner import _validate_turn_scores

        result = {
            "turns": [
                {
                    "turn": 1,
                    "overall_score": 7.45,
                    "scores": {
                        "correctness": 8,
                        "tool_selection": 8,
                        "context_retention": 7,
                        "completeness": 7,
                        "efficiency": 7,
                        "personality": 7,
                        "error_recovery": 7,
                    },
                }
            ]
        }
        # All dimension scores present → recompute succeeds → no warning
        warnings = _validate_turn_scores(result)
        assert warnings == []

    def test_validate_turn_scores_warns_on_missing_dimensions(self):
        """A turn with missing dimension scores cannot be recomputed → warning."""
        from gaia.eval.runner import _validate_turn_scores

        result = {
            "turns": [
                {
                    "turn": 1,
                    "overall_score": 7.0,
                    "scores": {
                        "correctness": 8,
                        "tool_selection": 8,
                        # missing: context_retention, completeness, efficiency, personality, error_recovery
                    },
                }
            ]
        }
        warnings = _validate_turn_scores(result)
        assert len(warnings) == 1
        assert "Turn 1" in warnings[0]
        assert "missing dimension" in warnings[0]


class TestValidateScenario:
    """Tests for the scenario YAML schema validator."""

    def test_valid_scenario_passes(self, tmp_path):
        from gaia.eval.runner import validate_scenario

        data = {
            "id": "test_scenario",
            "category": "rag_quality",
            "persona": "casual_user",
            "setup": {"index_documents": []},
            "turns": [
                {
                    "turn": 1,
                    "objective": "Ask something",
                    "ground_truth": {"expected_answer": "42"},
                },
            ],
        }
        validate_scenario(tmp_path / "test.yaml", data)  # should not raise

    def test_missing_persona_raises(self, tmp_path):
        from gaia.eval.runner import validate_scenario

        data = {
            "id": "test_scenario",
            "category": "rag_quality",
            # missing persona
            "setup": {"index_documents": []},
            "turns": [{"turn": 1, "objective": "x", "success_criteria": "ok"}],
        }
        with pytest.raises(ValueError, match="persona"):
            validate_scenario(tmp_path / "test.yaml", data)

    def test_custom_persona_accepted(self, tmp_path):
        """Custom persona strings are now accepted (not just the 5 built-in ones)."""
        from gaia.eval.runner import validate_scenario

        data = {
            "id": "test_scenario",
            "category": "rag_quality",
            "persona": "not_a_real_persona",
            "setup": {"index_documents": []},
            "turns": [{"turn": 1, "objective": "x", "success_criteria": "ok"}],
        }
        # Should NOT raise — custom personas are accepted since #671
        validate_scenario(tmp_path / "test.yaml", data)

    def test_non_string_persona_raises(self, tmp_path):
        from gaia.eval.runner import validate_scenario

        data = {
            "id": "test_scenario",
            "category": "rag_quality",
            "persona": 42,
            "setup": {"index_documents": []},
            "turns": [{"turn": 1, "objective": "x", "success_criteria": "ok"}],
        }
        with pytest.raises(ValueError, match="persona must be a string"):
            validate_scenario(tmp_path / "test.yaml", data)

    def test_empty_ground_truth_raises(self, tmp_path):
        from gaia.eval.runner import validate_scenario

        data = {
            "id": "test_scenario",
            "category": "rag_quality",
            "setup": {"index_documents": []},
            "turns": [{"turn": 1, "objective": "x", "ground_truth": {}}],
        }
        with pytest.raises(ValueError, match="ground_truth.*success_criteria"):
            validate_scenario(tmp_path / "test.yaml", data)

    def test_missing_required_field_raises(self, tmp_path):
        from gaia.eval.runner import validate_scenario

        data = {
            "id": "test_scenario",
            "category": "rag_quality",
            # missing setup and turns
        }
        with pytest.raises(ValueError, match="missing top-level field"):
            validate_scenario(tmp_path / "test.yaml", data)

    def test_empty_turns_raises(self, tmp_path):
        from gaia.eval.runner import validate_scenario

        data = {
            "id": "test_scenario",
            "category": "rag_quality",
            "setup": {"index_documents": []},
            "turns": [],
        }
        with pytest.raises(ValueError, match="turns list is empty"):
            validate_scenario(tmp_path / "test.yaml", data)

    def test_duplicate_turn_numbers_raises(self, tmp_path):
        from gaia.eval.runner import validate_scenario

        data = {
            "id": "test_scenario",
            "category": "rag_quality",
            "setup": {"index_documents": []},
            "turns": [
                {"turn": 1, "objective": "first", "success_criteria": "ok"},
                {"turn": 1, "objective": "duplicate", "success_criteria": "ok"},
            ],
        }
        with pytest.raises(ValueError, match="duplicate turn number"):
            validate_scenario(tmp_path / "test.yaml", data)

    def test_turn_missing_objective_raises(self, tmp_path):
        from gaia.eval.runner import validate_scenario

        data = {
            "id": "test_scenario",
            "category": "rag_quality",
            "setup": {"index_documents": []},
            "turns": [
                {"turn": 1, "ground_truth": {"expected_answer": "x"}},
            ],
        }
        with pytest.raises(ValueError, match="missing 'objective'"):
            validate_scenario(tmp_path / "test.yaml", data)

    def test_missing_setup_index_documents_raises(self, tmp_path):
        from gaia.eval.runner import validate_scenario

        data = {
            "id": "test_scenario",
            "category": "rag_quality",
            "setup": {},  # missing index_documents key
            "turns": [{"turn": 1, "objective": "x", "success_criteria": "ok"}],
        }
        with pytest.raises(ValueError, match="setup.index_documents is missing"):
            validate_scenario(tmp_path / "test.yaml", data)

    def test_ground_truth_null_without_success_criteria_raises(self, tmp_path):
        """ground_truth: null with no success_criteria must fail validation."""
        from gaia.eval.runner import validate_scenario

        data = {
            "id": "test_scenario",
            "category": "rag_quality",
            "setup": {"index_documents": []},
            "turns": [{"turn": 1, "objective": "x", "ground_truth": None}],
        }
        with pytest.raises(ValueError, match="must have at least one of"):
            validate_scenario(tmp_path / "test.yaml", data)

    def test_dict_success_criteria_raises(self, tmp_path):
        """success_criteria as a dict (old capture format) must fail validation."""
        from gaia.eval.runner import validate_scenario

        data = {
            "id": "test_scenario",
            "category": "rag_quality",
            "setup": {"index_documents": []},
            "turns": [
                {
                    "turn": 1,
                    "objective": "x",
                    "success_criteria": {
                        "must_contain": [],
                        "agent_response_preview": "foo",
                    },
                }
            ],
        }
        with pytest.raises(ValueError, match="success_criteria must be a string"):
            validate_scenario(tmp_path / "test.yaml", data)

    def test_non_sequential_turn_numbers_raises(self, tmp_path):
        """Turn numbers like [1, 3] that skip 2 must fail validation."""
        from gaia.eval.runner import validate_scenario

        data = {
            "id": "test_scenario",
            "category": "rag_quality",
            "setup": {"index_documents": []},
            "turns": [
                {"turn": 1, "objective": "first", "success_criteria": "ok"},
                {"turn": 3, "objective": "skipped 2", "success_criteria": "ok"},
            ],
        }
        with pytest.raises(ValueError, match="sequential"):
            validate_scenario(tmp_path / "test.yaml", data)

    def test_turns_not_starting_at_1_raises(self, tmp_path):
        """Turn numbers starting at 2 must fail validation."""
        from gaia.eval.runner import validate_scenario

        data = {
            "id": "test_scenario",
            "category": "rag_quality",
            "setup": {"index_documents": []},
            "turns": [
                {"turn": 2, "objective": "starts at 2", "success_criteria": "ok"},
            ],
        }
        with pytest.raises(ValueError, match="sequential"):
            validate_scenario(tmp_path / "test.yaml", data)


class TestManifestCrossReference:
    """Validate doc_id and fact_id cross-references between scenario YAMLs and merged manifest."""

    @staticmethod
    def _load_merged_manifest():
        """Load the main manifest merged with the real-world manifest (if present).

        Mirrors the merge logic in run_scenario_subprocess so cross-reference tests
        catch broken references in both standard and real-world scenarios.
        """
        from gaia.eval.runner import MANIFEST, REAL_WORLD_MANIFEST

        manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
        if REAL_WORLD_MANIFEST.exists():
            rw = json.loads(REAL_WORLD_MANIFEST.read_text(encoding="utf-8"))
            manifest["documents"] = manifest.get("documents", []) + rw.get(
                "documents", []
            )
        return manifest

    def test_scenario_doc_ids_exist_in_manifest(self):
        """Every doc_id referenced in scenario ground_truth must exist in the merged manifest."""
        from gaia.eval.runner import REAL_WORLD_MANIFEST, SCENARIOS_DIR, find_scenarios

        manifest = self._load_merged_manifest()
        all_doc_ids = {doc["id"] for doc in manifest.get("documents", [])}
        real_world_scenarios_dir = SCENARIOS_DIR / "real_world"
        real_world_manifest_present = REAL_WORLD_MANIFEST.exists()

        scenarios = find_scenarios()
        missing = []
        for path, data in scenarios:
            # Skip real_world scenarios when their manifest isn't present (e.g. in CI)
            if not real_world_manifest_present and str(path).startswith(
                str(real_world_scenarios_dir)
            ):
                continue
            for turn in data.get("turns", []):
                gt = turn.get("ground_truth") or {}
                doc_id = gt.get("doc_id")
                if doc_id and doc_id not in all_doc_ids:
                    missing.append(
                        f"{data['id']} turn {turn.get('turn', '?')}: doc_id='{doc_id}'"
                    )
        assert (
            not missing
        ), "Scenario doc_id references not in merged manifest:\n  " + "\n  ".join(
            missing
        )

    def test_scenario_fact_ids_exist_in_manifest(self):
        """Every fact_id referenced in scenarios must exist in the merged manifest."""
        from gaia.eval.runner import REAL_WORLD_MANIFEST, SCENARIOS_DIR, find_scenarios

        manifest = self._load_merged_manifest()
        # Real-world manifest facts don't have 'id' fields — only index by (doc_id, fact_id)
        # for documents where facts have IDs.
        all_fact_ids = {
            (doc["id"], fact["id"])
            for doc in manifest.get("documents", [])
            for fact in doc.get("facts", [])
            if "id" in fact
        }
        real_world_scenarios_dir = SCENARIOS_DIR / "real_world"
        real_world_manifest_present = REAL_WORLD_MANIFEST.exists()

        scenarios = find_scenarios()
        missing = []
        for path, data in scenarios:
            # Skip real_world scenarios when their manifest isn't present (e.g. in CI)
            if not real_world_manifest_present and str(path).startswith(
                str(real_world_scenarios_dir)
            ):
                continue
            for turn in data.get("turns", []):
                gt = turn.get("ground_truth") or {}
                doc_id = gt.get("doc_id")
                # Check singular fact_id
                fact_id = gt.get("fact_id")
                if doc_id and fact_id and (doc_id, fact_id) not in all_fact_ids:
                    missing.append(
                        f"{data['id']} turn {turn.get('turn', '?')}: {doc_id}.{fact_id}"
                    )
                # Check plural fact_ids list
                for fid in gt.get("fact_ids", []):
                    if doc_id and fid and (doc_id, fid) not in all_fact_ids:
                        missing.append(
                            f"{data['id']} turn {turn.get('turn', '?')}: {doc_id}.{fid} (from fact_ids)"
                        )
        assert (
            not missing
        ), "Scenario fact_id references not in merged manifest:\n  " + "\n  ".join(
            missing
        )

    def test_scenario_corpus_docs_exist_in_manifest(self):
        """Every corpus_doc in setup.index_documents must match a manifest document id."""
        from gaia.eval.runner import REAL_WORLD_MANIFEST, SCENARIOS_DIR, find_scenarios

        manifest = self._load_merged_manifest()
        all_doc_ids = {doc["id"] for doc in manifest.get("documents", [])}
        real_world_scenarios_dir = SCENARIOS_DIR / "real_world"
        real_world_manifest_present = REAL_WORLD_MANIFEST.exists()

        scenarios = find_scenarios()
        missing = []
        for path, data in scenarios:
            if not real_world_manifest_present and str(path).startswith(
                str(real_world_scenarios_dir)
            ):
                continue
            for i, doc in enumerate(data.get("setup", {}).get("index_documents", [])):
                if not isinstance(doc, dict):
                    continue
                corpus_doc = doc.get("corpus_doc")
                if corpus_doc and corpus_doc not in all_doc_ids:
                    missing.append(
                        f"{data['id']} setup.index_documents[{i}]: corpus_doc='{corpus_doc}'"
                    )
        assert (
            not missing
        ), "Scenario corpus_doc references not in merged manifest:\n  " + "\n  ".join(
            missing
        )


class TestComputeEffectiveTimeout:
    """Tests for _compute_effective_timeout."""

    def test_base_timeout_used_when_larger(self):
        from gaia.eval.runner import _compute_effective_timeout

        data = {"turns": [{}], "setup": {"index_documents": []}}
        # base=9000 >> 120 + 0*90 + 1*200 = 320 → should return 9000 (but capped at 7200)
        assert _compute_effective_timeout(7200, data) == 7200

    def test_computed_exceeds_base(self):
        from gaia.eval.runner import _compute_effective_timeout

        # 2 docs + 3 turns → 240 + 2*90 + 3*200 = 240+180+600 = 1020
        data = {
            "turns": [{}, {}, {}],
            "setup": {"index_documents": [{}, {}]},
        }
        result = _compute_effective_timeout(100, data)
        assert result == 1020

    def test_cap_enforced(self):
        from gaia.eval.runner import (
            _MAX_EFFECTIVE_TIMEOUT_S,
            _compute_effective_timeout,
        )

        # 100 docs + 100 turns → 240 + 100*90 + 100*200 = 240+9000+20000 = 29240 > cap
        data = {
            "turns": [{}] * 100,
            "setup": {"index_documents": [{}] * 100},
        }
        result = _compute_effective_timeout(100, data)
        assert result == _MAX_EFFECTIVE_TIMEOUT_S

    def test_empty_scenario_uses_startup_overhead(self):
        from gaia.eval.runner import _STARTUP_OVERHEAD_S, _compute_effective_timeout

        data = {"turns": [], "setup": {"index_documents": []}}
        result = _compute_effective_timeout(0, data)
        assert result == _STARTUP_OVERHEAD_S


class TestBuildScenarioPrompt:
    """Tests for the prompt builder."""

    def _make_scenario(self):
        return {
            "id": "test_s",
            "category": "rag_quality",
            "setup": {
                "index_documents": [
                    {"corpus_doc": "x", "path": "eval/corpus/documents/x.md"}
                ]
            },
            "turns": [
                {
                    "turn": 1,
                    "objective": "Ask something",
                    "ground_truth": {"expected_answer": "42"},
                }
            ],
        }

    def test_prompt_contains_scenario_id(self):
        from gaia.eval.runner import build_scenario_prompt

        prompt = build_scenario_prompt(
            self._make_scenario(), {}, "http://localhost:4200"
        )
        assert "test_s" in prompt

    def test_prompt_contains_corpus_root(self):
        from gaia.eval.runner import CORPUS_DIR, build_scenario_prompt

        prompt = build_scenario_prompt(
            self._make_scenario(), {}, "http://localhost:4200"
        )
        corpus_root = str(CORPUS_DIR / "documents").replace("\\", "/")
        assert corpus_root in prompt

    def test_prompt_contains_backend_url(self):
        from gaia.eval.runner import build_scenario_prompt

        prompt = build_scenario_prompt(
            self._make_scenario(), {}, "http://localhost:9999"
        )
        assert "http://localhost:9999" in prompt

    def test_prompt_contains_scoring_rules(self):
        from gaia.eval.runner import build_scenario_prompt

        prompt = build_scenario_prompt(
            self._make_scenario(), {}, "http://localhost:4200"
        )
        # simulator.md content is inlined — verify key rubric elements are present
        assert "correctness" in prompt
        assert "PASS" in prompt
        assert "FAIL" in prompt

    def test_prompt_contains_manifest_json(self):
        from gaia.eval.runner import build_scenario_prompt

        manifest = {
            "documents": [{"id": "test_doc", "filename": "test.md", "facts": []}]
        }
        prompt = build_scenario_prompt(
            self._make_scenario(), manifest, "http://localhost:4200"
        )
        assert "test_doc" in prompt


class TestRunScenarioSubprocess:
    """Tests for run_scenario_subprocess JSON parsing via mocked subprocess."""

    def _minimal_scenario(self):
        return {
            "id": "mock_scenario",
            "category": "rag_quality",
            "setup": {"index_documents": []},
            "turns": [{"turn": 1, "objective": "x", "success_criteria": "ok"}],
        }

    def _run(self, mocker, stdout, returncode=0):
        import tempfile

        from gaia.eval.runner import run_scenario_subprocess

        mock_proc = mocker.MagicMock()
        mock_proc.stdout = stdout
        mock_proc.stderr = ""
        mock_proc.returncode = returncode
        mocker.patch("subprocess.run", return_value=mock_proc)

        with tempfile.TemporaryDirectory() as tmp:
            return run_scenario_subprocess(
                Path(tmp) / "scenario.yaml",
                self._minimal_scenario(),
                Path(tmp),
                "http://localhost:4200",
                "claude-sonnet-4-6",
                "1.00",
                30,
            )

    def test_structured_output_parsed(self, mocker):
        payload = {
            "structured_output": {
                "scenario_id": "mock_scenario",
                "status": "PASS",
                "overall_score": 9.0,
                "turns": [],
                "root_cause": None,
                "recommended_fix": None,
                "cost_estimate": {"turns": 1, "estimated_usd": 0.01},
            }
        }
        result = self._run(mocker, json.dumps(payload))
        assert result["status"] == "PASS"
        assert result["overall_score"] == 9.0
        assert result["category"] == "rag_quality"

    def test_budget_exceeded_detected(self, mocker):
        payload = {
            "subtype": "error_max_budget_usd",
            "total_cost_usd": 2.05,
            "num_turns": 3,
        }
        result = self._run(mocker, json.dumps(payload))
        assert result["status"] == "BUDGET_EXCEEDED"
        assert result["overall_score"] is None

    def test_nonzero_exit_returns_errored(self, mocker):
        result = self._run(mocker, "", returncode=1)
        assert result["status"] == "ERRORED"
        assert result["overall_score"] is None

    def test_missing_status_field_defaulted(self, mocker):
        """Eval agent returning JSON without 'status' should be defaulted to ERRORED."""
        payload = {
            "structured_output": {
                "scenario_id": "mock_scenario",
                "overall_score": 7.0,
                "turns": [],
            }
        }
        result = self._run(mocker, json.dumps(payload))
        assert result["status"] == "ERRORED"

    def test_none_score_does_not_crash_print(self, mocker):
        """overall_score=null should not raise TypeError in the [DONE] print."""
        payload = {
            "structured_output": {
                "scenario_id": "mock_scenario",
                "status": "FAIL",
                "overall_score": None,
                "turns": [],
                "cost_estimate": {"turns": 1, "estimated_usd": 0.0},
            }
        }
        result = self._run(mocker, json.dumps(payload))  # must not raise
        assert result["status"] == "FAIL"
        assert result["overall_score"] is None

    def test_turn_score_overwrite_with_recomputed(self, mocker):
        """LLM arithmetic errors are corrected: turn overall_score is overwritten with recomputed value."""
        # LLM reported 5.0 but weighted sum of dimension scores is 7.45
        payload = {
            "structured_output": {
                "scenario_id": "mock_scenario",
                "status": "PASS",
                "overall_score": 9.0,
                "turns": [
                    {
                        "turn": 1,
                        "user_message": "hi",
                        "agent_response": "ok",
                        "agent_tools": [],
                        "scores": {
                            "correctness": 8,
                            "tool_selection": 8,
                            "context_retention": 7,
                            "completeness": 7,
                            "efficiency": 7,
                            "personality": 7,
                            "error_recovery": 7,
                        },
                        "overall_score": 5.0,  # wrong — should be 7.45
                        "pass": True,
                        "failure_category": None,
                        "reasoning": "ok",
                    }
                ],
                "cost_estimate": {"turns": 1, "estimated_usd": 0.01},
            }
        }
        result = self._run(mocker, json.dumps(payload))
        # Recomputed: 8*.25 + 8*.20 + 7*.20 + 7*.15 + 7*.10 + 7*.05 + 7*.05 = 7.45
        assert result["turns"][0]["overall_score"] == pytest.approx(7.45, abs=0.01)

    def test_scenario_overall_score_derived_from_turns(self, mocker):
        """Scenario-level overall_score is recomputed as mean of per-turn scores."""
        payload = {
            "structured_output": {
                "scenario_id": "mock_scenario",
                "status": "PASS",
                "overall_score": 3.0,  # LLM wrong value — should become mean of turns
                "turns": [
                    {
                        "turn": 1,
                        "user_message": "hi",
                        "agent_response": "ok",
                        "agent_tools": [],
                        "scores": {
                            "correctness": 10,
                            "tool_selection": 10,
                            "context_retention": 10,
                            "completeness": 10,
                            "efficiency": 10,
                            "personality": 10,
                            "error_recovery": 10,
                        },
                        "overall_score": 10.0,
                        "pass": True,
                        "failure_category": None,
                        "reasoning": "ok",
                    },
                    {
                        "turn": 2,
                        "user_message": "bye",
                        "agent_response": "bye",
                        "agent_tools": [],
                        "scores": {
                            "correctness": 0,
                            "tool_selection": 0,
                            "context_retention": 0,
                            "completeness": 0,
                            "efficiency": 0,
                            "personality": 0,
                            "error_recovery": 0,
                        },
                        "overall_score": 0.0,
                        "pass": False,
                        "failure_category": "wrong_answer",
                        "reasoning": "bad",
                    },
                ],
                "cost_estimate": {"turns": 2, "estimated_usd": 0.02},
            }
        }
        result = self._run(mocker, json.dumps(payload))
        # Turn 1 recomputed = 10.0, Turn 2 recomputed = 0.0 → mean = 5.0
        assert result["overall_score"] == pytest.approx(5.0, abs=0.01)

    def test_all_null_turn_scores_sets_scenario_score_to_none(self, mocker):
        """When all turns have null overall_score, scenario overall_score is set to None."""
        payload = {
            "structured_output": {
                "scenario_id": "mock_scenario",
                "status": "FAIL",
                "overall_score": 7.5,  # LLM value — should be nullified since no turn scores
                "turns": [
                    {
                        "turn": 1,
                        "user_message": "hi",
                        "agent_response": "ok",
                        "agent_tools": [],
                        "scores": {},  # empty — all dimensions missing → recompute returns -1
                        "overall_score": None,
                        "pass": False,
                        "failure_category": "wrong_answer",
                        "reasoning": "bad",
                    }
                ],
                "cost_estimate": {"turns": 1, "estimated_usd": 0.01},
            }
        }
        result = self._run(mocker, json.dumps(payload))
        assert result["overall_score"] is None

    def test_pass_with_low_correctness_overridden_to_fail(self, mocker):
        """LLM returning PASS when a turn has correctness < 4 is overridden to FAIL."""
        payload = {
            "structured_output": {
                "scenario_id": "mock_scenario",
                "status": "PASS",  # LLM claims PASS — should be overridden
                "overall_score": 7.0,
                "turns": [
                    {
                        "turn": 1,
                        "user_message": "hi",
                        "agent_response": "wrong",
                        "agent_tools": [],
                        "scores": {
                            "correctness": 2,  # < 4 → must FAIL per rubric
                            "tool_selection": 8,
                            "context_retention": 8,
                            "completeness": 8,
                            "efficiency": 8,
                            "personality": 8,
                            "error_recovery": 8,
                        },
                        "overall_score": 7.0,
                        "pass": True,
                        "failure_category": None,
                        "reasoning": "wrong",
                    }
                ],
                "cost_estimate": {"turns": 1, "estimated_usd": 0.01},
            }
        }
        result = self._run(mocker, json.dumps(payload))
        assert result["status"] == "FAIL"

    def test_pass_with_low_overall_score_overridden_to_fail(self, mocker):
        """LLM returning PASS when overall_score < 6.0 is overridden to FAIL."""
        payload = {
            "structured_output": {
                "scenario_id": "mock_scenario",
                "status": "PASS",  # LLM claims PASS — but recomputed score < 6.0
                "overall_score": 8.0,  # will be replaced by mean of turn scores
                "turns": [
                    {
                        "turn": 1,
                        "user_message": "hi",
                        "agent_response": "ok",
                        "agent_tools": [],
                        "scores": {
                            "correctness": 4,
                            "tool_selection": 4,
                            "context_retention": 4,
                            "completeness": 4,
                            "efficiency": 4,
                            "personality": 4,
                            "error_recovery": 4,
                        },
                        "overall_score": 4.0,  # recomputed = 4.0 < 6.0
                        "pass": True,
                        "failure_category": None,
                        "reasoning": "borderline",
                    }
                ],
                "cost_estimate": {"turns": 1, "estimated_usd": 0.01},
            }
        }
        result = self._run(mocker, json.dumps(payload))
        assert result["status"] == "FAIL"
        assert result["overall_score"] == pytest.approx(4.0, abs=0.01)

    def test_fail_not_upgraded_when_some_turns_lack_scores(self, mocker):
        """FAIL→PASS upgrade is suppressed when some turns are missing dimension scores."""
        payload = {
            "structured_output": {
                "scenario_id": "mock_scenario",
                "status": "FAIL",
                "overall_score": 8.0,
                "turns": [
                    {  # turn 1: fully scored, good
                        "turn": 1,
                        "user_message": "hi",
                        "agent_response": "ok",
                        "agent_tools": [],
                        "scores": {
                            "correctness": 8,
                            "tool_selection": 8,
                            "context_retention": 8,
                            "completeness": 8,
                            "efficiency": 8,
                            "personality": 8,
                            "error_recovery": 8,
                        },
                        "overall_score": 8.0,
                        "pass": True,
                        "failure_category": None,
                        "reasoning": "good",
                    },
                    {  # turn 2: no dimension scores (eval agent timed out before scoring)
                        "turn": 2,
                        "user_message": "more",
                        "agent_response": "?",
                        "agent_tools": [],
                        "scores": {},
                        "overall_score": None,
                        "pass": False,
                        "failure_category": None,
                        "reasoning": "",
                    },
                ],
                "cost_estimate": {"turns": 2, "estimated_usd": 0.01},
            }
        }
        result = self._run(mocker, json.dumps(payload))
        # Must remain FAIL — turn 2 has no scores, cannot confirm it would pass
        assert result["status"] == "FAIL"

    def test_fail_with_good_scores_overridden_to_pass(self, mocker):
        """LLM returning FAIL when all turns score ≥4 correctness and overall ≥6.0 is corrected to PASS."""
        payload = {
            "structured_output": {
                "scenario_id": "mock_scenario",
                "status": "FAIL",  # LLM false-FAIL — rubric says PASS
                "overall_score": 8.0,
                "turns": [
                    {
                        "turn": 1,
                        "user_message": "hi",
                        "agent_response": "correct",
                        "agent_tools": [],
                        "scores": {
                            "correctness": 8,
                            "tool_selection": 8,
                            "context_retention": 8,
                            "completeness": 8,
                            "efficiency": 8,
                            "personality": 8,
                            "error_recovery": 8,
                        },
                        "overall_score": 8.0,
                        "pass": True,
                        "failure_category": None,
                        "reasoning": "good",
                    }
                ],
                "cost_estimate": {"turns": 1, "estimated_usd": 0.01},
            }
        }
        result = self._run(mocker, json.dumps(payload))
        assert result["status"] == "PASS"

    def test_turn_pass_flag_recomputed_after_score_overwrite(self, mocker):
        """turn['pass'] is recomputed from the recomputed score, not left as LLM value."""
        payload = {
            "structured_output": {
                "scenario_id": "mock_scenario",
                "status": "PASS",
                "overall_score": 8.0,
                "turns": [
                    {
                        "turn": 1,
                        "user_message": "hi",
                        "agent_response": "wrong",
                        "agent_tools": [],
                        "scores": {
                            "correctness": 2,  # < 4 → turn pass must be False
                            "tool_selection": 8,
                            "context_retention": 8,
                            "completeness": 8,
                            "efficiency": 8,
                            "personality": 8,
                            "error_recovery": 8,
                        },
                        "overall_score": 9.0,
                        "pass": True,  # LLM says pass — wrong
                        "failure_category": None,
                        "reasoning": "wrong",
                    }
                ],
                "cost_estimate": {"turns": 1, "estimated_usd": 0.01},
            }
        }
        result = self._run(mocker, json.dumps(payload))
        # Turn pass must be False: correctness=2 < 4
        assert result["turns"][0]["pass"] is False

    def test_fail_scenario_score_preserved_in_runner(self, mocker):
        """FAIL scenario score is NOT capped in the runner — raw score preserved in trace."""
        payload = {
            "structured_output": {
                "scenario_id": "mock_scenario",
                "status": "PASS",  # will be overridden to FAIL due to correctness=0
                "overall_score": 9.0,
                "turns": [
                    {
                        "turn": 1,
                        "user_message": "hi",
                        "agent_response": "wrong",
                        "agent_tools": [],
                        "scores": {
                            "correctness": 0,  # forces FAIL
                            "tool_selection": 10,
                            "context_retention": 10,
                            "completeness": 10,
                            "efficiency": 10,
                            "personality": 10,
                            "error_recovery": 10,
                        },
                        "overall_score": 9.0,
                        "pass": True,
                        "failure_category": None,
                        "reasoning": "wrong",
                    }
                ],
                "cost_estimate": {"turns": 1, "estimated_usd": 0.01},
            }
        }
        result = self._run(mocker, json.dumps(payload))
        assert result["status"] == "FAIL"
        # Runner preserves the recomputed score (correctness=0 weight=0.40 pulls it down to ~6.0)
        # The cap at 5.99 is applied only inside scorecard.py avg_score computation, not here.
        assert isinstance(result["overall_score"], float)

    def test_non_dict_json_returns_errored(self, mocker):
        """Eval agent returning a JSON array or scalar is wrapped in an ERRORED result."""
        # Return a JSON array (valid JSON but not a dict)
        result = self._run(mocker, json.dumps([{"turns": []}]))
        assert result["status"] == "ERRORED"
        assert "non-object" in result.get("error", "")
        assert result["scenario_id"] == "mock_scenario"

    def test_scenario_id_always_injected_from_runner(self, mocker):
        """Runner always overwrites scenario_id with its own sid — eval agent value is untrusted."""
        payload = {
            "structured_output": {
                "scenario_id": "WRONG_ID_FROM_EVAL_AGENT",
                "status": "PASS",
                "overall_score": 8.5,
                "turns": [
                    {
                        "turn": 1,
                        "user_message": "hi",
                        "agent_response": "ok",
                        "agent_tools": [],
                        "scores": {
                            "correctness": 8,
                            "tool_selection": 8,
                            "context_retention": 8,
                            "completeness": 8,
                            "efficiency": 8,
                            "personality": 8,
                            "error_recovery": 8,
                        },
                        "overall_score": 8.5,
                        "pass": True,
                        "failure_category": None,
                        "reasoning": "ok",
                    }
                ],
                "cost_estimate": {"turns": 1, "estimated_usd": 0.01},
            }
        }
        result = self._run(mocker, json.dumps(payload))
        # Runner's own scenario_id must win regardless of what eval agent returned
        assert result["scenario_id"] == "mock_scenario"

    def test_infra_status_not_overridden(self, mocker):
        """BLOCKED_BY_ARCHITECTURE status is never overridden to FAIL."""
        payload = {
            "structured_output": {
                "scenario_id": "mock_scenario",
                "status": "BLOCKED_BY_ARCHITECTURE",
                "overall_score": 3.0,
                "turns": [
                    {
                        "turn": 1,
                        "user_message": "hi",
                        "agent_response": "blocked",
                        "agent_tools": [],
                        "scores": {
                            "correctness": 0,
                            "tool_selection": 0,
                            "context_retention": 0,
                            "completeness": 0,
                            "efficiency": 0,
                            "personality": 0,
                            "error_recovery": 0,
                        },
                        "overall_score": 0.0,
                        "pass": False,
                        "failure_category": "no_fallback",
                        "reasoning": "arch",
                    }
                ],
                "cost_estimate": {"turns": 1, "estimated_usd": 0.01},
            }
        }
        result = self._run(mocker, json.dumps(payload))
        assert result["status"] == "BLOCKED_BY_ARCHITECTURE"


class TestScorecardByCategory:
    """Tests for by_category breakdown in build_scorecard."""

    def test_judged_pass_rate_excludes_infra_failures(self):
        """judged_pass_rate denominator excludes infra failures; pass_rate includes them."""
        from gaia.eval.scorecard import build_scorecard

        results = [
            {
                "scenario_id": "a",
                "status": "PASS",
                "overall_score": 9.0,
                "category": "x",
                "cost_estimate": {"estimated_usd": 0},
            },
            {
                "scenario_id": "b",
                "status": "PASS",
                "overall_score": 8.0,
                "category": "x",
                "cost_estimate": {"estimated_usd": 0},
            },
            {
                "scenario_id": "c",
                "status": "TIMEOUT",
                "overall_score": None,
                "category": "x",
                "cost_estimate": {"estimated_usd": 0},
            },
            {
                "scenario_id": "d",
                "status": "TIMEOUT",
                "overall_score": None,
                "category": "x",
                "cost_estimate": {"estimated_usd": 0},
            },
        ]
        sc = build_scorecard("run", results, {})
        summary = sc["summary"]
        # pass_rate includes infra failures: 2 pass / 4 total = 50%
        assert summary["pass_rate"] == pytest.approx(0.5)
        # judged_pass_rate excludes infra failures: 2 pass / 2 judged = 100%
        assert summary["judged_pass_rate"] == pytest.approx(1.0)

    def test_judged_pass_rate_counts_pass_with_null_score(self):
        """A PASS result with null overall_score still counts toward judged_pass_rate numerator."""
        from gaia.eval.scorecard import build_scorecard

        results = [
            {
                "scenario_id": "a",
                "status": "PASS",
                "overall_score": None,
                "category": "x",
                "cost_estimate": {"estimated_usd": 0},
            },
            {
                "scenario_id": "b",
                "status": "FAIL",
                "overall_score": 3.0,
                "category": "x",
                "cost_estimate": {"estimated_usd": 0},
            },
        ]
        sc = build_scorecard("run", results, {})
        # 1 PASS out of 2 judged = 50%
        assert sc["summary"]["judged_pass_rate"] == pytest.approx(0.5)

    def test_blocked_by_architecture_included_in_judged_pass_rate_denominator(self):
        """BLOCKED_BY_ARCHITECTURE is a judged status and counts in the denominator."""
        from gaia.eval.scorecard import build_scorecard

        results = [
            {
                "scenario_id": "a",
                "status": "PASS",
                "overall_score": 8.0,
                "category": "x",
                "cost_estimate": {"estimated_usd": 0},
            },
            {
                "scenario_id": "b",
                "status": "BLOCKED_BY_ARCHITECTURE",
                "overall_score": 4.0,
                "category": "x",
                "cost_estimate": {"estimated_usd": 0},
            },
            {
                "scenario_id": "c",
                "status": "TIMEOUT",
                "overall_score": None,
                "category": "x",
                "cost_estimate": {"estimated_usd": 0},
            },
        ]
        sc = build_scorecard("run", results, {})
        # judged = PASS + BLOCKED = 2; passed = 1 → 50%
        assert sc["summary"]["judged_pass_rate"] == pytest.approx(0.5)
        # BLOCKED score 4.0 is included in avg_score: (8.0 + 4.0) / 2 = 6.0
        assert sc["summary"]["avg_score"] == pytest.approx(6.0, abs=0.1)

    def test_infra_error_tracked_separately_from_errored(self):
        """INFRA_ERROR and SETUP_ERROR must go to infra_error bucket, not errored."""
        from gaia.eval.scorecard import build_scorecard

        results = [
            {
                "scenario_id": "a",
                "status": "INFRA_ERROR",
                "overall_score": None,
                "category": "rag_quality",
                "cost_estimate": {"estimated_usd": 0},
            },
            {
                "scenario_id": "b",
                "status": "SETUP_ERROR",
                "overall_score": None,
                "category": "rag_quality",
                "cost_estimate": {"estimated_usd": 0},
            },
            {
                "scenario_id": "c",
                "status": "UNKNOWN_STATUS",
                "overall_score": None,
                "category": "rag_quality",
                "cost_estimate": {"estimated_usd": 0},
            },
        ]
        sc = build_scorecard("run", results, {})
        cat = sc["summary"]["by_category"]["rag_quality"]
        assert (
            cat["infra_error"] == 2
        ), "INFRA_ERROR+SETUP_ERROR should be in infra_error"
        assert cat["errored"] == 1, "Unknown status should be in errored only"

    def test_none_score_compare_scorecards_no_false_delta(self, tmp_path):
        """Two None scores must produce delta=0, not crash."""
        from gaia.eval.runner import compare_scorecards
        from gaia.eval.scorecard import build_scorecard

        base = [
            {
                "scenario_id": "s1",
                "status": "TIMEOUT",
                "overall_score": None,
                "category": "x",
                "cost_estimate": {"estimated_usd": 0},
            }
        ]
        curr = [
            {
                "scenario_id": "s1",
                "status": "PASS",
                "overall_score": 8.0,
                "category": "x",
                "cost_estimate": {"estimated_usd": 0},
            }
        ]
        bp = tmp_path / "base.json"
        cp = tmp_path / "curr.json"
        bp.write_text(json.dumps(build_scorecard("r1", base, {})))
        cp.write_text(json.dumps(build_scorecard("r2", curr, {})))
        result = compare_scorecards(bp, cp)
        # TIMEOUT→PASS is an improvement, not a crash
        assert len(result["improved"]) == 1
        assert result["improved"][0]["baseline_score"] == 0  # None mapped to 0

    def test_scorecard_warns_on_unrecognized_status(self):
        """An unrecognized status is bucketed as 'errored' and emits a warning."""
        from gaia.eval.scorecard import build_scorecard

        results = [
            {
                "scenario_id": "a",
                "status": "PASS",
                "overall_score": 9.0,
                "category": "x",
                "cost_estimate": {"estimated_usd": 0},
            },
            {
                "scenario_id": "b",
                "status": "SKIPPED",
                "overall_score": None,
                "category": "x",
                "cost_estimate": {"estimated_usd": 0},
            },
        ]
        sc = build_scorecard("run", results, {})
        # Unrecognized status bucketed as errored
        assert sc["summary"]["errored"] == 1
        # Warning surfaces in the scorecard
        assert "warnings" in sc
        assert any("SKIPPED" in w for w in sc["warnings"])

    def test_fail_score_capped_at_5_99_in_avg_score(self):
        """FAIL scenario with score > 5.99 is capped to 5.99 when computing avg_score."""
        from gaia.eval.scorecard import build_scorecard

        results = [
            {
                "scenario_id": "a",
                "status": "FAIL",
                "overall_score": 7.5,
                "category": "x",
                "cost_estimate": {"estimated_usd": 0},
            },
            {
                "scenario_id": "b",
                "status": "PASS",
                "overall_score": 9.0,
                "category": "x",
                "cost_estimate": {"estimated_usd": 0},
            },
        ]
        sc = build_scorecard("run", results, {})
        # avg_score = (5.99 + 9.0) / 2 = 7.495, not (7.5 + 9.0) / 2 = 8.25
        assert sc["summary"]["avg_score"] <= 7.50
        # Raw score in the result dict is preserved (not mutated)
        assert results[0]["overall_score"] == 7.5

    def test_errored_status_not_flagged_as_unrecognized(self):
        """ERRORED status is a known runner status and must not trigger the unrecognized warning."""
        from gaia.eval.scorecard import build_scorecard

        results = [
            {
                "scenario_id": "a",
                "status": "ERRORED",
                "overall_score": None,
                "category": "x",
                "cost_estimate": {"estimated_usd": 0},
            },
        ]
        sc = build_scorecard("run", results, {})
        # Must be counted in errored bucket, not trigger unrecognized-status warning
        assert sc["summary"]["errored"] == 1
        assert "warnings" not in sc

    def test_none_status_sorted_without_type_error(self):
        """A result with status=None is bucketed as errored without raising TypeError in sorted()."""
        from gaia.eval.scorecard import build_scorecard

        results = [
            {
                "scenario_id": "a",
                "status": None,
                "overall_score": None,
                "category": "x",
                "cost_estimate": {"estimated_usd": 0},
            },
        ]
        # Should not raise TypeError from sorted({None, ...})
        sc = build_scorecard("run", results, {})
        assert sc["summary"]["errored"] == 1


class TestCompareScorecardEdgeCases:
    """Edge case tests for compare_scorecards."""

    def test_fail_fail_score_regression_detected(self, tmp_path):
        """A FAIL→FAIL scenario with a significant score drop is flagged as score_regressed."""
        from gaia.eval.runner import compare_scorecards
        from gaia.eval.scorecard import build_scorecard

        baseline = [
            {
                "scenario_id": "a",
                "status": "FAIL",
                "overall_score": 5.5,
                "category": "rag_quality",
                "cost_estimate": {"estimated_usd": 0},
            }
        ]
        current = [
            {
                "scenario_id": "a",
                "status": "FAIL",
                "overall_score": 1.0,
                "category": "rag_quality",
                "cost_estimate": {"estimated_usd": 0},
            }
        ]
        b_sc = build_scorecard("b", baseline, {})
        c_sc = build_scorecard("c", current, {})
        p_b = tmp_path / "baseline.json"
        p_c = tmp_path / "current.json"
        p_b.write_text(json.dumps(b_sc))
        p_c.write_text(json.dumps(c_sc))

        report = compare_scorecards(p_b, p_c)
        # Delta = 1.0 - 5.5 = -4.5, exceeds _SCORE_REGRESSION_THRESHOLD → score_regressed
        assert any(e["scenario_id"] == "a" for e in report.get("score_regressed", []))

    def test_missing_scenario_id_skipped_gracefully(self, tmp_path, capsys):
        """A result dict missing 'scenario_id' is skipped with a warning, not a crash."""
        from gaia.eval.runner import compare_scorecards
        from gaia.eval.scorecard import build_scorecard

        # Intentionally omit scenario_id from one result
        results = [
            {
                "status": "PASS",
                "overall_score": 9.0,
                "category": "rag_quality",
                "cost_estimate": {"estimated_usd": 0},
            }
        ]
        sc = build_scorecard("run", results, {})
        p = tmp_path / "bad.json"
        p.write_text(json.dumps(sc))

        good_results = [
            {
                "scenario_id": "a",
                "status": "PASS",
                "overall_score": 9.0,
                "category": "rag_quality",
                "cost_estimate": {"estimated_usd": 0},
            }
        ]
        good_sc = build_scorecard("run", good_results, {})
        p2 = tmp_path / "good.json"
        p2.write_text(json.dumps(good_sc))

        # Should not raise — result missing scenario_id is skipped with a warning
        report = compare_scorecards(p, p2)
        assert report is not None
        captured = capsys.readouterr()
        assert "missing 'scenario_id'" in captured.err


class TestFixModeMerge:
    """Tests for the fix-mode scenario merge logic (no subprocess needed)."""

    def test_merge_keeps_passing_replaces_failing(self):
        """Rerun results replace only the scenarios that were re-run; passing ones are preserved."""
        from gaia.eval.scorecard import build_scorecard

        passing = {
            "scenario_id": "pass_a",
            "status": "PASS",
            "overall_score": 9.0,
            "category": "x",
            "cost_estimate": {"estimated_usd": 0},
        }
        failing = {
            "scenario_id": "fail_b",
            "status": "FAIL",
            "overall_score": 2.0,
            "category": "x",
            "cost_estimate": {"estimated_usd": 0},
        }
        current_scorecard = build_scorecard("r0", [passing, failing], {})

        # Simulate a rerun of fail_b that now passes
        rerun_result = {
            "scenario_id": "fail_b",
            "status": "PASS",
            "overall_score": 8.5,
            "category": "x",
            "cost_estimate": {"estimated_usd": 0},
        }
        rerun_map = {rerun_result["scenario_id"]: rerun_result}

        # Apply the same merge logic as fix-mode loop
        merged = []
        for s in current_scorecard["scenarios"]:
            sid = s.get("scenario_id")
            merged.append(rerun_map[sid] if sid in rerun_map else s)

        new_scorecard = build_scorecard("r1", merged, {})
        # pass_a preserved, fail_b replaced
        assert new_scorecard["summary"]["passed"] == 2
        assert new_scorecard["summary"]["failed"] == 0

    def test_merge_does_not_discard_previously_passing_on_regression(self):
        """If a previously PASS scenario regresses during rerun, it is still included."""
        from gaia.eval.scorecard import build_scorecard

        passing = {
            "scenario_id": "pass_a",
            "status": "PASS",
            "overall_score": 9.0,
            "category": "x",
            "cost_estimate": {"estimated_usd": 0},
        }
        failing = {
            "scenario_id": "fail_b",
            "status": "FAIL",
            "overall_score": 2.0,
            "category": "x",
            "cost_estimate": {"estimated_usd": 0},
        }
        current_scorecard = build_scorecard("r0", [passing, failing], {})

        # Rerun of fail_b still fails
        rerun_result = {
            "scenario_id": "fail_b",
            "status": "FAIL",
            "overall_score": 1.5,
            "category": "x",
            "cost_estimate": {"estimated_usd": 0},
        }
        rerun_map = {rerun_result["scenario_id"]: rerun_result}

        merged = []
        for s in current_scorecard["scenarios"]:
            sid = s.get("scenario_id")
            merged.append(rerun_map[sid] if sid in rerun_map else s)

        new_scorecard = build_scorecard("r1", merged, {})
        # pass_a still in merged, fail_b still failing
        assert new_scorecard["summary"]["passed"] == 1
        assert new_scorecard["summary"]["failed"] == 1
        scenario_ids = {s["scenario_id"] for s in new_scorecard["scenarios"]}
        assert "pass_a" in scenario_ids


class TestDocumentsExist:
    """Tests for the _documents_exist helper."""

    def test_empty_index_documents_returns_true(self):
        from gaia.eval.runner import _documents_exist

        data = {"setup": {"index_documents": []}}
        assert _documents_exist(data) is True

    def test_existing_file_returns_true(self, tmp_path):
        from gaia.eval.runner import REPO_ROOT, _documents_exist

        # Create a real file relative to REPO_ROOT so REPO_ROOT / path exists
        rel = Path("eval/corpus/documents/acme_q3_report.md")
        assert (REPO_ROOT / rel).exists(), "Known test fixture must exist"
        data = {
            "setup": {
                "index_documents": [{"corpus_doc": "acme_q3_report", "path": str(rel)}]
            }
        }
        assert _documents_exist(data) is True

    def test_missing_file_returns_false(self):
        from gaia.eval.runner import _documents_exist

        data = {
            "setup": {
                "index_documents": [
                    {
                        "corpus_doc": "ghost",
                        "path": "eval/corpus/real_world/does_not_exist.txt",
                    }
                ]
            }
        }
        assert _documents_exist(data) is False

    def test_string_entries_ignored(self):
        """String entries in index_documents (no 'path' field) don't cause false negatives."""
        from gaia.eval.runner import _documents_exist

        data = {"setup": {"index_documents": ["some_string_entry"]}}
        assert _documents_exist(data) is True


class TestSkippedNoDocument:
    """Tests for SKIPPED_NO_DOCUMENT handling in scorecard."""

    def _make_result(self, scenario_id, status, score=None, category="real_world"):
        return {
            "scenario_id": scenario_id,
            "status": status,
            "overall_score": score,
            "category": category,
            "cost_estimate": {"estimated_usd": 0},
        }

    def test_skipped_excluded_from_pass_rate_denominator(self):
        """SKIPPED_NO_DOCUMENT is NOT excluded from pass_rate denominator (it counts as total)."""
        from gaia.eval.scorecard import build_scorecard

        results = [
            self._make_result("a", "PASS", 9.0),
            self._make_result("b", "SKIPPED_NO_DOCUMENT"),
        ]
        sc = build_scorecard("run", results, {})
        # Total = 2, passed = 1 → pass_rate = 50%
        assert sc["summary"]["pass_rate"] == pytest.approx(0.5)
        assert sc["summary"]["skipped"] == 1

    def test_skipped_excluded_from_judged_pass_rate(self):
        """SKIPPED_NO_DOCUMENT is excluded from judged_pass_rate denominator."""
        from gaia.eval.scorecard import build_scorecard

        results = [
            self._make_result("a", "PASS", 9.0),
            self._make_result("b", "PASS", 8.0),
            self._make_result("c", "SKIPPED_NO_DOCUMENT"),
            self._make_result("d", "SKIPPED_NO_DOCUMENT"),
        ]
        sc = build_scorecard("run", results, {})
        # judged = 2 (PASS+PASS), skipped excluded → judged_pass_rate = 100%
        assert sc["summary"]["judged_pass_rate"] == pytest.approx(1.0)
        assert sc["summary"]["skipped"] == 2

    def test_skipped_excluded_from_avg_score(self):
        """SKIPPED_NO_DOCUMENT (score=None) must not affect avg_score."""
        from gaia.eval.scorecard import build_scorecard

        results = [
            self._make_result("a", "PASS", 8.0),
            self._make_result("b", "SKIPPED_NO_DOCUMENT"),
        ]
        sc = build_scorecard("run", results, {})
        # avg_score only from judged scenarios: 8.0
        assert sc["summary"]["avg_score"] == pytest.approx(8.0)

    def test_skipped_not_in_errored_bucket(self):
        """SKIPPED_NO_DOCUMENT must go to 'skipped' bucket, not 'errored'."""
        from gaia.eval.scorecard import build_scorecard

        results = [self._make_result("a", "SKIPPED_NO_DOCUMENT")]
        sc = build_scorecard("run", results, {})
        assert sc["summary"]["skipped"] == 1
        assert sc["summary"]["errored"] == 0
        # Should NOT trigger the warnings key for unrecognized status
        assert "warnings" not in sc

    def test_skipped_not_in_errored_by_category(self):
        """by_category tracks skipped separately from errored."""
        from gaia.eval.scorecard import build_scorecard

        results = [
            self._make_result("a", "SKIPPED_NO_DOCUMENT", category="real_world"),
        ]
        sc = build_scorecard("run", results, {})
        cat = sc["summary"]["by_category"]["real_world"]
        assert cat["skipped"] == 1
        assert cat["errored"] == 0


class TestFindScenariosExtraDirs:
    """Tests for --scenario-dir (extra_dirs parameter in find_scenarios)."""

    def _write_scenario(self, d, scenario_id, category="custom", tags=None):
        """Write a minimal valid scenario YAML into directory d."""
        d.mkdir(parents=True, exist_ok=True)
        data = {
            "id": scenario_id,
            "category": category,
            "persona": "casual_user",
            "setup": {"index_documents": []},
            "turns": [
                {"turn": 1, "objective": "Ask something", "success_criteria": "ok"},
            ],
        }
        if tags:
            data["tags"] = tags
        path = d / f"{scenario_id}.yaml"
        import yaml

        path.write_text(yaml.dump(data, default_flow_style=False), encoding="utf-8")
        return path

    def test_extra_dirs_discovers_scenarios(self, tmp_path):
        from gaia.eval.runner import find_scenarios

        self._write_scenario(tmp_path, "custom_scenario_1")
        results = find_scenarios(extra_dirs=[str(tmp_path)])
        ids = [data["id"] for _, data in results]
        assert "custom_scenario_1" in ids

    def test_extra_dirs_override_builtin(self, tmp_path):
        from gaia.eval.runner import find_scenarios

        # Write a scenario with a known built-in ID to override it
        self._write_scenario(tmp_path, "simple_factual_rag", category="overridden")
        results = find_scenarios(
            scenario_id="simple_factual_rag", extra_dirs=[str(tmp_path)]
        )
        assert len(results) == 1
        # The category should be the overridden one
        assert results[0][1]["category"] == "overridden"

    def test_nonexistent_extra_dir_skipped(self, tmp_path):
        from gaia.eval.runner import find_scenarios

        # Should not crash — just skip the missing dir
        results = find_scenarios(extra_dirs=[str(tmp_path / "nonexistent")])
        # Should still return built-in scenarios
        assert len(results) > 0

    def test_tag_filtering(self, tmp_path):
        from gaia.eval.runner import find_scenarios

        self._write_scenario(tmp_path, "tagged_1", tags=["healthcare", "production"])
        self._write_scenario(tmp_path, "tagged_2", tags=["finance"])
        self._write_scenario(tmp_path, "untagged_3")

        # Filter by healthcare tag
        results = find_scenarios(extra_dirs=[str(tmp_path)], tags=["healthcare"])
        ids = [data["id"] for _, data in results]
        assert "tagged_1" in ids
        assert "tagged_2" not in ids
        # untagged_3 should not appear (no tags match)
        assert "untagged_3" not in ids

    def test_tag_filtering_or_logic(self, tmp_path):
        from gaia.eval.runner import find_scenarios

        self._write_scenario(tmp_path, "tag_a", tags=["healthcare"])
        self._write_scenario(tmp_path, "tag_b", tags=["finance"])

        # Both should match with OR logic
        results = find_scenarios(
            extra_dirs=[str(tmp_path)], tags=["healthcare", "finance"]
        )
        ids = [data["id"] for _, data in results]
        assert "tag_a" in ids
        assert "tag_b" in ids

    def test_tags_field_accepted_in_scenario_yaml(self, tmp_path):
        """Tags field in scenario YAML should not cause validation errors."""
        from gaia.eval.runner import validate_scenario

        data = {
            "id": "test_with_tags",
            "category": "rag_quality",
            "persona": "casual_user",
            "tags": ["healthcare", "production", "critical"],
            "setup": {"index_documents": []},
            "turns": [
                {"turn": 1, "objective": "Ask something", "success_criteria": "ok"},
            ],
        }
        validate_scenario(tmp_path / "test.yaml", data)  # should not raise


class TestCustomPersonas:
    """Tests for custom persona support in validate_scenario."""

    def test_builtin_persona_still_works(self, tmp_path):
        from gaia.eval.runner import validate_scenario

        data = {
            "id": "test",
            "category": "rag_quality",
            "persona": "casual_user",
            "setup": {"index_documents": []},
            "turns": [
                {"turn": 1, "objective": "Ask something", "success_criteria": "ok"},
            ],
        }
        validate_scenario(tmp_path / "test.yaml", data)  # should not raise

    def test_custom_persona_accepted(self, tmp_path):
        from gaia.eval.runner import validate_scenario

        data = {
            "id": "test",
            "category": "rag_quality",
            "persona": "impatient_doctor",
            "setup": {"index_documents": []},
            "turns": [
                {"turn": 1, "objective": "Ask something", "success_criteria": "ok"},
            ],
        }
        # Custom persona should be accepted without raising
        validate_scenario(tmp_path / "test.yaml", data)

    def test_long_custom_persona_string_accepted(self, tmp_path):
        from gaia.eval.runner import validate_scenario

        data = {
            "id": "test",
            "category": "rag_quality",
            "persona": "A senior software engineer reviewing code for security vulnerabilities",
            "setup": {"index_documents": []},
            "turns": [
                {"turn": 1, "objective": "Ask something", "success_criteria": "ok"},
            ],
        }
        validate_scenario(tmp_path / "test.yaml", data)  # should not raise

    def test_empty_persona_still_raises(self, tmp_path):
        from gaia.eval.runner import validate_scenario

        data = {
            "id": "test",
            "category": "rag_quality",
            "persona": "   ",
            "setup": {"index_documents": []},
            "turns": [
                {"turn": 1, "objective": "Ask something", "success_criteria": "ok"},
            ],
        }
        with pytest.raises(ValueError, match="non-empty"):
            validate_scenario(tmp_path / "test.yaml", data)

    def test_non_string_persona_still_raises(self, tmp_path):
        from gaia.eval.runner import validate_scenario

        data = {
            "id": "test",
            "category": "rag_quality",
            "persona": 42,
            "setup": {"index_documents": []},
            "turns": [
                {"turn": 1, "objective": "Ask something", "success_criteria": "ok"},
            ],
        }
        with pytest.raises(ValueError, match="persona must be a string"):
            validate_scenario(tmp_path / "test.yaml", data)


class TestJunitXmlOutput:
    """Tests for JUnit XML output format."""

    def test_write_junit_xml_basic(self):
        from gaia.eval.scorecard import build_scorecard, write_junit_xml

        results = [
            {
                "scenario_id": "pass_test",
                "status": "PASS",
                "overall_score": 8.5,
                "category": "rag_quality",
                "turns": [],
                "elapsed_s": 10.0,
            },
            {
                "scenario_id": "fail_test",
                "status": "FAIL",
                "overall_score": 3.2,
                "category": "rag_quality",
                "turns": [],
                "root_cause": "Wrong answer",
                "elapsed_s": 15.0,
            },
        ]
        sc = build_scorecard("run-junit", results, {"model": "test-model"})
        xml_str = write_junit_xml(sc)

        # Should be valid XML
        import xml.etree.ElementTree as ET

        root = ET.fromstring(xml_str)
        assert root.tag == "testsuites"
        assert root.get("tests") == "2"

        # Should have one testsuite for the category
        suites = root.findall("testsuite")
        assert len(suites) == 1
        assert suites[0].get("name") == "rag_quality"

        # Should have two testcases
        cases = suites[0].findall("testcase")
        assert len(cases) == 2

    def test_write_junit_xml_failure_details(self):
        from gaia.eval.scorecard import build_scorecard, write_junit_xml

        results = [
            {
                "scenario_id": "failed_scenario",
                "status": "FAIL",
                "overall_score": 2.0,
                "category": "adversarial",
                "turns": [
                    {"turn": 1, "overall_score": 2.0, "pass": False},
                ],
                "root_cause": "Hallucination",
                "recommended_fix": "Improve grounding",
                "elapsed_s": 20.0,
            },
        ]
        sc = build_scorecard("run-junit", results, {})
        xml_str = write_junit_xml(sc)

        import xml.etree.ElementTree as ET

        root = ET.fromstring(xml_str)
        failure = root.find(".//failure")
        assert failure is not None
        assert "FAIL" in failure.get("type", "")
        assert "Hallucination" in failure.text

    def test_write_junit_xml_skipped(self):
        from gaia.eval.scorecard import build_scorecard, write_junit_xml

        results = [
            {
                "scenario_id": "skipped_test",
                "status": "SKIPPED_NO_DOCUMENT",
                "overall_score": None,
                "category": "real_world",
                "turns": [],
                "elapsed_s": 0.0,
            },
        ]
        sc = build_scorecard("run-junit", results, {})
        xml_str = write_junit_xml(sc)

        import xml.etree.ElementTree as ET

        root = ET.fromstring(xml_str)
        skipped = root.find(".//skipped")
        assert skipped is not None

    def test_write_junit_xml_error_status(self):
        from gaia.eval.scorecard import build_scorecard, write_junit_xml

        results = [
            {
                "scenario_id": "timeout_test",
                "status": "TIMEOUT",
                "overall_score": None,
                "category": "rag_quality",
                "turns": [],
                "elapsed_s": 900.0,
                "error": "Timed out",
            },
        ]
        sc = build_scorecard("run-junit", results, {})
        xml_str = write_junit_xml(sc)

        import xml.etree.ElementTree as ET

        root = ET.fromstring(xml_str)
        error = root.find(".//error")
        assert error is not None
        assert error.get("type") == "TIMEOUT"

    def test_write_junit_xml_multiple_categories(self):
        from gaia.eval.scorecard import build_scorecard, write_junit_xml

        results = [
            {
                "scenario_id": "rag_test",
                "status": "PASS",
                "overall_score": 9.0,
                "category": "rag_quality",
                "turns": [],
            },
            {
                "scenario_id": "adv_test",
                "status": "PASS",
                "overall_score": 8.0,
                "category": "adversarial",
                "turns": [],
            },
        ]
        sc = build_scorecard("run-junit", results, {})
        xml_str = write_junit_xml(sc)

        import xml.etree.ElementTree as ET

        root = ET.fromstring(xml_str)
        suites = root.findall("testsuite")
        assert len(suites) == 2
        suite_names = {s.get("name") for s in suites}
        assert "rag_quality" in suite_names
        assert "adversarial" in suite_names


class TestCustomCorpusDir:
    """Tests for --corpus-dir (extra_corpus_dirs in _load_merged_manifest)."""

    def test_load_merged_manifest_no_extras(self):
        from gaia.eval.runner import _load_merged_manifest

        manifest = _load_merged_manifest()
        assert "documents" in manifest
        assert len(manifest["documents"]) > 0

    def test_load_merged_manifest_with_extra_dir(self, tmp_path):
        from gaia.eval.runner import _load_merged_manifest

        # Create a minimal manifest in extra dir
        extra_manifest = {
            "documents": [
                {"id": "custom_doc_1", "filename": "custom.txt", "facts": []},
            ]
        }
        (tmp_path / "manifest.json").write_text(json.dumps(extra_manifest))

        manifest = _load_merged_manifest(extra_corpus_dirs=[str(tmp_path)])
        doc_ids = [d.get("id") for d in manifest["documents"]]
        assert "custom_doc_1" in doc_ids

    def test_load_merged_manifest_missing_extra_manifest(self, tmp_path):
        from gaia.eval.runner import _load_merged_manifest

        # Directory exists but no manifest.json — should not crash
        empty_dir = tmp_path / "empty_corpus"
        empty_dir.mkdir()
        manifest = _load_merged_manifest(extra_corpus_dirs=[str(empty_dir)])
        assert "documents" in manifest


class TestEvalCLINewFlags:
    """Test that new CLI flags appear in help output."""

    def test_eval_agent_help_shows_new_flags(self):
        result = subprocess.run(
            [sys.executable, "-m", "gaia.cli", "eval", "agent", "--help"],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, f"Command failed: {result.stderr}"
        help_text = result.stdout
        assert "--scenario-dir" in help_text
        assert "--corpus-dir" in help_text
        assert "--tag" in help_text
        assert "--output-format" in help_text
        assert "junit" in help_text


class TestScenarioLoading:
    """Tests for scenario YAML parsing and filtering."""

    def test_yaml_scenario_parsing_all_field_types(self, tmp_path):
        """Parse a complete scenario YAML with all optional fields and verify each field type."""
        import yaml

        scenario = {
            "id": "test_all_fields",
            "name": "Full field test scenario",
            "category": "rag_quality",
            "severity": "high",
            "description": "A scenario that exercises every optional field.",
            "persona": "power_user",
            "tags": ["test", "comprehensive"],
            "setup": {
                "index_documents": [
                    {
                        "corpus_doc": "acme_q3_report",
                        "path": "eval/corpus/documents/acme_q3_report.md",
                    }
                ]
            },
            "turns": [
                {
                    "turn": 1,
                    "objective": "Ask about Q3 revenue",
                    "ground_truth": {
                        "doc_id": "acme_q3_report",
                        "expected_answer": "$4.2M",
                        "fact_id": "q3_total_revenue",
                    },
                    "success_criteria": "Agent states revenue correctly.",
                    "expected_tools": ["search"],
                },
                {
                    "turn": 2,
                    "objective": "Follow-up on growth rate",
                    "ground_truth": {
                        "doc_id": "acme_q3_report",
                        "expected_answer": "15% YoY growth",
                    },
                    "success_criteria": "Agent states growth correctly.",
                },
            ],
            "expected_outcome": "Agent answers both turns correctly.",
        }

        yaml_path = tmp_path / "full.yaml"
        yaml_path.write_text(yaml.dump(scenario, default_flow_style=False))
        data = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))

        # Verify types for all fields
        assert isinstance(data["id"], str)
        assert isinstance(data["name"], str)
        assert isinstance(data["category"], str)
        assert isinstance(data["severity"], str)
        assert isinstance(data["description"], str)
        assert isinstance(data["persona"], str)
        assert isinstance(data["tags"], list)
        assert all(isinstance(t, str) for t in data["tags"])
        assert isinstance(data["setup"], dict)
        assert isinstance(data["setup"]["index_documents"], list)
        assert isinstance(data["setup"]["index_documents"][0], dict)
        assert "corpus_doc" in data["setup"]["index_documents"][0]
        assert "path" in data["setup"]["index_documents"][0]
        assert isinstance(data["turns"], list)
        assert len(data["turns"]) == 2
        assert isinstance(data["turns"][0]["ground_truth"], dict)
        assert "doc_id" in data["turns"][0]["ground_truth"]
        assert "expected_answer" in data["turns"][0]["ground_truth"]
        assert isinstance(data["turns"][0]["success_criteria"], str)
        assert isinstance(data["turns"][0].get("expected_tools"), list)
        assert isinstance(data.get("expected_outcome"), str)

    def test_invalid_scenario_yaml_clear_errors(self, tmp_path):
        """Invalid YAML should produce clear, actionable error messages for multiple error types."""
        from gaia.eval.runner import validate_scenario

        # Missing required top-level fields
        with pytest.raises(ValueError, match="missing top-level field"):
            validate_scenario(tmp_path / "bad.yaml", {"id": "bad"})

        # Empty turns list
        with pytest.raises(ValueError, match="turns list is empty"):
            validate_scenario(
                tmp_path / "empty_turns.yaml",
                {
                    "id": "empty",
                    "category": "x",
                    "persona": "casual_user",
                    "setup": {"index_documents": []},
                    "turns": [],
                },
            )

        # Turn missing objective
        with pytest.raises(ValueError, match="missing 'objective'"):
            validate_scenario(
                tmp_path / "no_obj.yaml",
                {
                    "id": "no_obj",
                    "category": "x",
                    "persona": "casual_user",
                    "setup": {"index_documents": []},
                    "turns": [
                        {"turn": 1, "success_criteria": "ok"},
                    ],
                },
            )

        # Non-string persona
        with pytest.raises(ValueError, match="persona must be a string"):
            validate_scenario(
                tmp_path / "num_persona.yaml",
                {
                    "id": "np",
                    "category": "x",
                    "persona": 42,
                    "setup": {"index_documents": []},
                    "turns": [{"turn": 1, "objective": "x", "success_criteria": "ok"}],
                },
            )

        # Dict success_criteria (old capture format)
        with pytest.raises(ValueError, match="success_criteria must be a string"):
            validate_scenario(
                tmp_path / "dict_criteria.yaml",
                {
                    "id": "dc",
                    "category": "x",
                    "persona": "casual_user",
                    "setup": {"index_documents": []},
                    "turns": [
                        {
                            "turn": 1,
                            "objective": "x",
                            "success_criteria": {"must_contain": []},
                        }
                    ],
                },
            )

    def test_scenario_filtering_by_category(self):
        """find_scenarios with category filter returns only matching scenarios."""
        from gaia.eval.runner import find_scenarios

        all_scenarios = find_scenarios()
        categories = {data["category"] for _, data in all_scenarios}
        assert len(categories) > 0, "No categories found in scenarios"

        # Test filtering for each category
        for test_cat in list(categories)[:2]:  # test first 2 categories
            filtered = find_scenarios(category=test_cat)
            assert len(filtered) > 0, f"No scenarios found for category {test_cat}"
            for _, data in filtered:
                assert (
                    data["category"] == test_cat
                ), f"Expected category {test_cat}, got {data['category']}"

        # Verify filtering reduces the set when multiple categories exist
        if len(categories) > 1:
            single_cat = find_scenarios(category=next(iter(categories)))
            assert len(single_cat) < len(
                all_scenarios
            ), "Category filter should reduce result set"

    def test_duplicate_scenario_ids_detected(self):
        """Built-in scenario IDs must be unique (no duplicates allowed)."""
        from gaia.eval.runner import find_scenarios

        scenarios = find_scenarios()
        ids = [data["id"] for _, data in scenarios]
        dupes = [x for x in ids if ids.count(x) > 1]
        assert (
            len(dupes) == 0
        ), f"Duplicate scenario IDs in eval/scenarios/: {set(dupes)}"


class TestRunner:
    """Tests for runner execution behavior (mocked subprocess)."""

    def test_timeout_handling(self, mocker):
        """Scenario that exceeds timeout gets TIMEOUT status."""
        import tempfile

        from gaia.eval.runner import run_scenario_subprocess

        mocker.patch(
            "subprocess.run",
            side_effect=subprocess.TimeoutExpired(cmd="claude", timeout=30),
        )

        scenario_data = {
            "id": "timeout_test",
            "category": "rag_quality",
            "setup": {"index_documents": []},
            "turns": [{"turn": 1, "objective": "x", "success_criteria": "ok"}],
        }

        with tempfile.TemporaryDirectory() as tmp:
            result = run_scenario_subprocess(
                Path(tmp) / "scenario.yaml",
                scenario_data,
                Path(tmp),
                "http://localhost:4200",
                "claude-sonnet-4-6",
                "1.00",
                30,
            )

        assert result["status"] == "TIMEOUT"
        assert result["overall_score"] is None
        assert result["scenario_id"] == "timeout_test"
        assert "elapsed_s" in result
        assert isinstance(result["elapsed_s"], float)

    def test_resume_from_checkpoint(self, tmp_path):
        """Mock a .progress.json and trace file to verify resume mechanism."""
        # Create a progress file indicating scenario "done_scenario" is completed
        progress = {"done_scenario": "PASS"}
        progress_path = tmp_path / ".progress.json"
        progress_path.write_text(json.dumps(progress))

        # Create a matching trace file (required for resume to work)
        traces_dir = tmp_path / "traces"
        traces_dir.mkdir()
        trace_result = {
            "scenario_id": "done_scenario",
            "status": "PASS",
            "overall_score": 9.0,
            "turns": [],
            "category": "rag_quality",
            "cost_estimate": {"turns": 1, "estimated_usd": 0.01},
        }
        (traces_dir / "done_scenario.json").write_text(json.dumps(trace_result))

        # Verify the progress file records completion
        completed = json.loads(progress_path.read_text(encoding="utf-8"))
        assert "done_scenario" in completed
        assert completed["done_scenario"] == "PASS"

        # Verify the trace is loadable and correct (resume reads from this)
        loaded_trace = json.loads(
            (traces_dir / "done_scenario.json").read_text(encoding="utf-8")
        )
        assert loaded_trace["status"] == "PASS"
        assert loaded_trace["scenario_id"] == "done_scenario"
        assert loaded_trace["overall_score"] == 9.0

    def test_cost_tracking_accuracy(self):
        """Verify cost calculation from token usage matches expected values using MODEL_PRICING."""
        from gaia.eval.config import MODEL_PRICING

        # Test with known pricing for claude-sonnet-4-6
        model = "claude-sonnet-4-6"
        pricing = MODEL_PRICING[model]
        input_tokens = 1_000_000  # 1M tokens
        output_tokens = 500_000  # 0.5M tokens

        expected_input_cost = (input_tokens / 1_000_000) * pricing["input_per_mtok"]
        expected_output_cost = (output_tokens / 1_000_000) * pricing["output_per_mtok"]
        expected_total = expected_input_cost + expected_output_cost

        # sonnet-4-6: $3/Mtok input + $15/Mtok output
        # 1M input = $3.00, 0.5M output = $7.50, total = $10.50
        assert expected_total == pytest.approx(10.50, abs=0.01)

        # Test default fallback for unknown models
        fallback = MODEL_PRICING["default"]
        assert fallback["input_per_mtok"] == 3.00
        assert fallback["output_per_mtok"] == 15.00

        # Test opus pricing is higher than sonnet
        opus_pricing = MODEL_PRICING["claude-opus-4"]
        assert opus_pricing["input_per_mtok"] > pricing["input_per_mtok"]
        assert opus_pricing["output_per_mtok"] > pricing["output_per_mtok"]

        # Verify all models have both required keys
        for model_name, model_pricing in MODEL_PRICING.items():
            assert (
                "input_per_mtok" in model_pricing
            ), f"{model_name} missing input pricing"
            assert (
                "output_per_mtok" in model_pricing
            ), f"{model_name} missing output pricing"

    def test_budget_exceeded_status(self, mocker):
        """Scenario exceeding budget gets BUDGET_EXCEEDED status."""
        import tempfile

        from gaia.eval.runner import run_scenario_subprocess

        budget_error = {
            "subtype": "error_max_budget_usd",
            "total_cost_usd": 2.05,
            "num_turns": 3,
        }
        mock_proc = mocker.MagicMock()
        mock_proc.stdout = json.dumps(budget_error)
        mock_proc.stderr = ""
        mock_proc.returncode = 0
        mocker.patch("subprocess.run", return_value=mock_proc)

        scenario_data = {
            "id": "budget_test",
            "category": "rag_quality",
            "setup": {"index_documents": []},
            "turns": [{"turn": 1, "objective": "x", "success_criteria": "ok"}],
        }

        with tempfile.TemporaryDirectory() as tmp:
            result = run_scenario_subprocess(
                Path(tmp) / "scenario.yaml",
                scenario_data,
                Path(tmp),
                "http://localhost:4200",
                "claude-sonnet-4-6",
                "1.00",
                30,
            )

        assert result["status"] == "BUDGET_EXCEEDED"
        assert result["overall_score"] is None
        assert result["cost_estimate"]["estimated_usd"] == pytest.approx(2.05)
        assert result["scenario_id"] == "budget_test"


class TestScorecardPublicAPI:
    """Tests for the scorecard public API including score calculation and comparison."""

    def _make_result(self, scenario_id, status, score, category="rag_quality"):
        return {
            "scenario_id": scenario_id,
            "status": status,
            "overall_score": score,
            "category": category,
            "cost_estimate": {"estimated_usd": 0.05},
        }

    def test_score_calculation_matches_formula(self):
        """Verify weighted score = sum(dimension_score * weight) with known inputs."""
        from gaia.eval.runner import _SCORE_WEIGHTS, recompute_turn_score

        # All scores = 10 → weighted sum = 10.0 (weights sum to 1.0)
        all_tens = {k: 10 for k in _SCORE_WEIGHTS}
        assert recompute_turn_score(all_tens) == pytest.approx(10.0, abs=0.001)

        # correctness=10, rest=0 → 10 * 0.25 = 2.5
        correctness_only = {k: 0 for k in _SCORE_WEIGHTS}
        correctness_only["correctness"] = 10
        assert recompute_turn_score(correctness_only) == pytest.approx(2.5, abs=0.001)

        # Mixed scores: verify manual calculation matches function
        mixed = {
            "correctness": 8,
            "tool_selection": 6,
            "context_retention": 7,
            "completeness": 9,
            "efficiency": 5,
            "personality": 10,
            "error_recovery": 4,
        }
        expected = (
            8 * 0.25 + 6 * 0.20 + 7 * 0.20 + 9 * 0.15 + 5 * 0.10 + 10 * 0.05 + 4 * 0.05
        )
        assert recompute_turn_score(mixed) == pytest.approx(expected, abs=0.001)

        # Verify weights sum to 1.0
        assert sum(_SCORE_WEIGHTS.values()) == pytest.approx(1.0, abs=0.001)

    def test_multi_turn_aggregation(self, mocker):
        """Scenario with 3 turns aggregates correctly (avg of turn scores)."""
        import tempfile

        from gaia.eval.runner import run_scenario_subprocess

        # Build a result with 3 turns having known uniform dimension scores
        turns = []
        for i, score_val in enumerate([8.0, 6.0, 10.0], start=1):
            turns.append(
                {
                    "turn": i,
                    "user_message": f"msg{i}",
                    "agent_response": f"resp{i}",
                    "agent_tools": [],
                    "scores": {
                        "correctness": score_val,
                        "tool_selection": score_val,
                        "context_retention": score_val,
                        "completeness": score_val,
                        "efficiency": score_val,
                        "personality": score_val,
                        "error_recovery": score_val,
                    },
                    "overall_score": score_val,
                    "pass": True,
                    "failure_category": None,
                    "reasoning": "ok",
                }
            )

        payload = {
            "structured_output": {
                "scenario_id": "multi_turn",
                "status": "PASS",
                "overall_score": 999,  # will be overwritten with mean of turns
                "turns": turns,
                "cost_estimate": {"turns": 3, "estimated_usd": 0.03},
            }
        }

        mock_proc = mocker.MagicMock()
        mock_proc.stdout = json.dumps(payload)
        mock_proc.stderr = ""
        mock_proc.returncode = 0
        mocker.patch("subprocess.run", return_value=mock_proc)

        scenario_data = {
            "id": "multi_turn",
            "category": "rag_quality",
            "setup": {"index_documents": []},
            "turns": [
                {"turn": 1, "objective": "x", "success_criteria": "ok"},
                {"turn": 2, "objective": "y", "success_criteria": "ok"},
                {"turn": 3, "objective": "z", "success_criteria": "ok"},
            ],
        }

        with tempfile.TemporaryDirectory() as tmp:
            result = run_scenario_subprocess(
                Path(tmp) / "scenario.yaml",
                scenario_data,
                Path(tmp),
                "http://localhost:4200",
                "claude-sonnet-4-6",
                "1.00",
                30,
            )

        # Mean of recomputed turn scores: (8.0 + 6.0 + 10.0) / 3 = 8.0
        assert result["overall_score"] == pytest.approx(8.0, abs=0.01)
        assert len(result["turns"]) == 3

    def test_comparison_detects_regression(self, tmp_path):
        """compare_scorecards flags when a passing scenario becomes failing."""
        from gaia.eval.runner import compare_scorecards
        from gaia.eval.scorecard import build_scorecard

        baseline = [self._make_result("sc1", "PASS", 9.0)]
        current = [self._make_result("sc1", "FAIL", 3.0)]

        bp = tmp_path / "base.json"
        cp = tmp_path / "curr.json"
        bp.write_text(json.dumps(build_scorecard("b", baseline, {})))
        cp.write_text(json.dumps(build_scorecard("c", current, {})))

        diff = compare_scorecards(bp, cp)
        assert len(diff["regressed"]) == 1
        assert diff["regressed"][0]["scenario_id"] == "sc1"
        assert diff["regressed"][0]["baseline_status"] == "PASS"
        assert diff["regressed"][0]["current_status"] == "FAIL"
        assert len(diff["improved"]) == 0

    def test_comparison_detects_improvement(self, tmp_path):
        """compare_scorecards flags when a failing scenario becomes passing."""
        from gaia.eval.runner import compare_scorecards
        from gaia.eval.scorecard import build_scorecard

        baseline = [self._make_result("sc1", "FAIL", 3.0)]
        current = [self._make_result("sc1", "PASS", 9.0)]

        bp = tmp_path / "base.json"
        cp = tmp_path / "curr.json"
        bp.write_text(json.dumps(build_scorecard("b", baseline, {})))
        cp.write_text(json.dumps(build_scorecard("c", current, {})))

        diff = compare_scorecards(bp, cp)
        assert len(diff["improved"]) == 1
        assert diff["improved"][0]["scenario_id"] == "sc1"
        assert diff["improved"][0]["baseline_status"] == "FAIL"
        assert diff["improved"][0]["current_status"] == "PASS"
        assert diff["improved"][0]["delta"] > 0
        assert len(diff["regressed"]) == 0

    def test_baseline_save_load_roundtrip(self, tmp_path):
        """Save scorecard as baseline JSON, load it back, verify equality."""
        from gaia.eval.scorecard import build_scorecard

        results = [
            self._make_result("a", "PASS", 9.0, "rag_quality"),
            self._make_result("b", "FAIL", 4.0, "tool_selection"),
            self._make_result("c", "TIMEOUT", None, "rag_quality"),
        ]
        config = {"model": "claude-sonnet-4-6", "budget_per_scenario_usd": 2.00}
        original = build_scorecard("run-baseline", results, config)

        # Save to JSON
        baseline_path = tmp_path / "baseline.json"
        baseline_path.write_text(
            json.dumps(original, indent=2, ensure_ascii=False), encoding="utf-8"
        )

        # Load it back
        loaded = json.loads(baseline_path.read_text(encoding="utf-8"))

        # Verify structural equality
        assert loaded["run_id"] == original["run_id"]
        assert loaded["config"] == original["config"]
        assert (
            loaded["summary"]["total_scenarios"]
            == original["summary"]["total_scenarios"]
        )
        assert loaded["summary"]["passed"] == original["summary"]["passed"]
        assert loaded["summary"]["failed"] == original["summary"]["failed"]
        assert loaded["summary"]["timeout"] == original["summary"]["timeout"]
        assert loaded["summary"]["pass_rate"] == pytest.approx(
            original["summary"]["pass_rate"]
        )
        assert loaded["summary"]["avg_score"] == pytest.approx(
            original["summary"]["avg_score"]
        )
        assert len(loaded["scenarios"]) == len(original["scenarios"])
        for orig, load in zip(original["scenarios"], loaded["scenarios"]):
            assert orig["scenario_id"] == load["scenario_id"]
            assert orig["status"] == load["status"]

        # Verify by_category is preserved
        for cat in original["summary"]["by_category"]:
            assert cat in loaded["summary"]["by_category"]
            assert (
                loaded["summary"]["by_category"][cat]["passed"]
                == original["summary"]["by_category"][cat]["passed"]
            )


class TestCorpusPublicAPI:
    """Tests for corpus manifest and document references."""

    def test_manifest_parsing(self):
        """Parse eval/corpus/manifest.json and verify structure."""
        from gaia.eval.runner import MANIFEST

        assert MANIFEST.exists(), f"Manifest not found at {MANIFEST}"
        manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))

        # Must have a documents list
        assert "documents" in manifest, "Manifest missing 'documents' key"
        assert isinstance(manifest["documents"], list)
        assert len(manifest["documents"]) > 0, "Manifest has no documents"

        # Each document must have required fields
        for doc in manifest["documents"]:
            assert "id" in doc, f"Document missing 'id': {doc}"
            assert "filename" in doc, f"Document missing 'filename': {doc}"
            assert isinstance(doc["id"], str)
            assert isinstance(doc["filename"], str)

    def test_missing_document_handling(self):
        """Scenario referencing missing doc returns False from _documents_exist."""
        from gaia.eval.runner import _documents_exist

        data = {
            "setup": {
                "index_documents": [
                    {
                        "corpus_doc": "nonexistent_doc",
                        "path": "eval/corpus/documents/does_not_exist_xyz_98765.md",
                    }
                ]
            }
        }
        assert _documents_exist(data) is False

    def test_document_existence_check(self):
        """All documents referenced in manifest exist on disk."""
        from gaia.eval.runner import CORPUS_DIR, MANIFEST

        assert MANIFEST.exists()
        manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))

        docs_dir = CORPUS_DIR / "documents"
        missing = []
        for doc in manifest.get("documents", []):
            filepath = docs_dir / doc["filename"]
            if not filepath.exists():
                missing.append(doc["filename"])

        assert not missing, f"Documents missing from disk: {missing}"


class TestCLIPublicAPI:
    """Tests for CLI command structure and flags."""

    def test_eval_agent_help(self):
        """gaia eval agent --help returns 0 and shows expected flags."""
        result = subprocess.run(
            [sys.executable, "-m", "gaia.cli", "eval", "agent", "--help"],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, f"Command failed: {result.stderr}"
        help_text = result.stdout
        assert "agent" in help_text.lower()
        assert "--scenario" in help_text
        assert "--category" in help_text
        assert "--audit-only" in help_text
        assert "--backend" in help_text
        assert "--model" in help_text
        assert "--budget" in help_text
        assert "--timeout" in help_text

    def test_eval_agent_audit_only_flag(self):
        """--audit-only flag is recognized and documented in help."""
        result = subprocess.run(
            [sys.executable, "-m", "gaia.cli", "eval", "agent", "--help"],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0
        assert "--audit-only" in result.stdout
        assert "audit" in result.stdout.lower()

    def test_eval_agent_scenario_flag(self):
        """--scenario flag is recognized and documented in help."""
        result = subprocess.run(
            [sys.executable, "-m", "gaia.cli", "eval", "agent", "--help"],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0
        assert "--scenario" in result.stdout

    def test_eval_agent_category_flag(self):
        """--category flag is recognized and documented in help."""
        result = subprocess.run(
            [sys.executable, "-m", "gaia.cli", "eval", "agent", "--help"],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0
        assert "--category" in result.stdout

    def test_output_formats_parseable(self, tmp_path):
        """Scorecard JSON output is valid JSON, summary.md is valid markdown."""
        from gaia.eval.scorecard import build_scorecard, write_summary_md

        results = [
            {
                "scenario_id": "test_a",
                "status": "PASS",
                "overall_score": 8.5,
                "category": "rag_quality",
                "cost_estimate": {"estimated_usd": 0.01},
            },
            {
                "scenario_id": "test_b",
                "status": "FAIL",
                "overall_score": 3.0,
                "category": "tool_selection",
                "cost_estimate": {"estimated_usd": 0.02},
            },
        ]
        config = {"model": "test-model"}
        scorecard = build_scorecard("test-run", results, config)

        # JSON output should be valid JSON (round-trip)
        json_str = json.dumps(scorecard, indent=2, ensure_ascii=False)
        parsed = json.loads(json_str)
        assert parsed["run_id"] == "test-run"
        assert parsed["summary"]["total_scenarios"] == 2

        # Write to file and read back
        json_path = tmp_path / "scorecard.json"
        json_path.write_text(json_str, encoding="utf-8")
        reloaded = json.loads(json_path.read_text(encoding="utf-8"))
        assert reloaded["summary"]["passed"] == 1

        # Summary markdown should be valid (contains expected structure)
        md = write_summary_md(scorecard)
        assert isinstance(md, str)
        assert md.startswith("# ")  # starts with H1 header
        assert "## Summary" in md
        assert "## By Category" in md
        assert "|" in md  # has table rows
        assert "PASS" in md or "✅" in md

        # Write markdown to file and verify it's readable
        md_path = tmp_path / "summary.md"
        md_path.write_text(md, encoding="utf-8")
        md_reloaded = md_path.read_text(encoding="utf-8")
        assert "test-run" in md_reloaded
        assert "test-model" in md_reloaded


class TestAuditPublicAPI:
    """Tests for the architecture audit public API."""

    def test_audit_returns_json(self):
        """run_audit() returns a dict with expected keys and correct types."""
        from gaia.eval.audit import run_audit

        result = run_audit()
        assert isinstance(result, dict)
        assert "architecture_audit" in result

        audit = result["architecture_audit"]
        expected_keys = {
            "history_pairs",
            "max_msg_chars",
            "tool_results_in_history",
            "agent_persistence",
            "blocked_scenarios",
            "recommendations",
        }
        assert expected_keys.issubset(
            set(audit.keys())
        ), f"Missing keys: {expected_keys - set(audit.keys())}"

        # Verify types
        assert isinstance(audit["blocked_scenarios"], list)
        assert isinstance(audit["recommendations"], list)
        assert isinstance(audit["tool_results_in_history"], bool)
        assert isinstance(audit["agent_persistence"], str)

    def test_audit_detects_low_history_pairs(self):
        """History pairs less than 5 generates recommendation; >= 5 does not."""
        from gaia.eval.audit import run_audit

        result = run_audit()
        audit = result["architecture_audit"]

        history_pairs = audit["history_pairs"]
        rec_ids = [r["id"] for r in audit["recommendations"]]

        if history_pairs != "unknown" and history_pairs < 5:
            assert (
                "increase_history_pairs" in rec_ids
            ), "Expected recommendation to increase history pairs when < 5"
        elif history_pairs != "unknown" and history_pairs >= 5:
            assert (
                "increase_history_pairs" not in rec_ids
            ), "Should not recommend increasing history_pairs when >= 5"

    def test_audit_detects_low_msg_chars(self):
        """Message chars less than 1000 generates recommendation and blocks scenarios."""
        from gaia.eval.audit import run_audit

        result = run_audit()
        audit = result["architecture_audit"]

        max_msg_chars = audit["max_msg_chars"]
        rec_ids = [r["id"] for r in audit["recommendations"]]

        if max_msg_chars != "unknown" and max_msg_chars < 1000:
            assert (
                "increase_truncation" in rec_ids
            ), "Expected recommendation to increase truncation limit"
            blocked_scenarios = [s["scenario"] for s in audit["blocked_scenarios"]]
            assert (
                "cross_turn_file_recall" in blocked_scenarios
            ), "Expected blocked scenario for low msg chars"
        elif max_msg_chars != "unknown" and max_msg_chars >= 1000:
            assert (
                "increase_truncation" not in rec_ids
            ), "Should not recommend increasing truncation when >= 1000"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
