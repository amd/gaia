# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Guard: a scorecard must name the agent that actually ran each scenario.

``scorecard.json`` records only the CLI-level ``--agent-type`` in its run config,
but a scenario's own ``agent_type:`` overrides that flag. A run invoked
``--agent-type gaia`` can therefore emit a scorecard labelled ``gaia`` whose
scenarios every one of them ran ``doc`` (#2983). These tests pin the resolved
per-scenario agent into each ``scenarios[]`` entry and prove the addition is
backward compatible with baselines recorded before the field existed.
"""

import json
from pathlib import Path

import pytest

from gaia.eval.runner import (
    _resolve_scenario_agent_type,
    compare_scorecards,
    run_scenario_subprocess,
)

REPO_ROOT = Path(__file__).resolve().parents[3]


class TestResolveScenarioAgentType:
    """The scenario's own ``agent_type:`` wins over the CLI default (AC-1)."""

    def test_scenario_yaml_overrides_cli_flag(self):
        assert _resolve_scenario_agent_type({"agent_type": "doc"}, "gaia") == "doc"

    def test_cli_flag_used_when_scenario_is_silent(self):
        assert _resolve_scenario_agent_type({}, "gaia") == "gaia"

    def test_blank_scenario_value_does_not_shadow_the_cli_flag(self):
        assert _resolve_scenario_agent_type({"agent_type": None}, "gaia") == "gaia"


class TestSubprocessResultCarriesAgentType:
    """``run_scenario_subprocess`` stamps the resolved agent onto every result.

    Both runner call sites (initial pass and the ``--fix`` rerun pass) go
    through this function, so stamping here covers both by construction.
    """

    @pytest.fixture
    def _stub_claude(self, monkeypatch, tmp_path):
        monkeypatch.setattr("gaia.eval.runner._load_merged_manifest", lambda **kw: {})
        monkeypatch.setattr(
            "gaia.eval.runner.build_scenario_prompt", lambda *a, **kw: "PROMPT"
        )

        class _Proc:
            returncode = 0
            stdout = json.dumps(
                {
                    "structured_output": {
                        "scenario_id": "s1",
                        "status": "PASS",
                        "overall_score": 8.0,
                        "turns": [],
                    }
                }
            )
            stderr = ""

        monkeypatch.setattr("gaia.eval.runner.subprocess.run", lambda *a, **kw: _Proc())
        return tmp_path

    def test_resolved_agent_is_recorded(self, _stub_claude):
        result = run_scenario_subprocess(
            _stub_claude / "s1.yaml",
            {"id": "s1", "category": "tool_selection", "agent_type": "doc"},
            _stub_claude,
            "http://localhost:4200",
            "some-model",
            "1.0",
            60,
            agent_type="doc",
        )
        assert result["agent_type"] == "doc"

    def test_agent_type_recorded_even_on_an_errored_run(self, monkeypatch, tmp_path):
        monkeypatch.setattr("gaia.eval.runner._load_merged_manifest", lambda **kw: {})
        monkeypatch.setattr(
            "gaia.eval.runner.build_scenario_prompt", lambda *a, **kw: "PROMPT"
        )

        class _Proc:
            returncode = 1
            stdout = ""
            stderr = "boom"

        monkeypatch.setattr("gaia.eval.runner.subprocess.run", lambda *a, **kw: _Proc())

        result = run_scenario_subprocess(
            tmp_path / "s1.yaml",
            {"id": "s1", "category": "tool_selection"},
            tmp_path,
            "http://localhost:4200",
            "some-model",
            "1.0",
            60,
            agent_type="gaia",
        )
        assert result["status"] == "ERRORED"
        assert result["agent_type"] == "gaia"


class TestBackwardCompatibleCompare:
    """A baseline recorded before the field existed still diffs normally.

    The committed baselines under ``tests/fixtures/eval_baselines/`` have no
    per-scenario ``agent_type``; ``--compare`` against them must not change
    verdict or raise.
    """

    def _write(self, path: Path, scenarios: list) -> Path:
        path.write_text(
            json.dumps({"run_id": "r", "config": {}, "scenarios": scenarios}),
            encoding="utf-8",
        )
        return path

    def test_old_baseline_vs_new_current(self, tmp_path):
        baseline = self._write(
            tmp_path / "baseline.json",
            [{"scenario_id": "s1", "status": "PASS", "overall_score": 8.0}],
        )
        current = self._write(
            tmp_path / "current.json",
            [
                {
                    "scenario_id": "s1",
                    "status": "PASS",
                    "overall_score": 8.0,
                    "agent_type": "doc",
                }
            ],
        )
        report = compare_scorecards(baseline, current)
        assert [e["scenario_id"] for e in report["unchanged"]] == ["s1"]
        assert report["regressed"] == []
        assert report["only_in_current"] == []

    def test_committed_baseline_parses_without_agent_type(self):
        baseline = (
            REPO_ROOT
            / "tests"
            / "fixtures"
            / "eval_baselines"
            / "gemma-4-e4b-d71cd914"
            / "scorecard_tool_selection.json"
        )
        data = json.loads(baseline.read_text(encoding="utf-8"))
        assert data["scenarios"], "committed baseline has no scenarios"
        assert not any("agent_type" in s for s in data["scenarios"]), (
            "this backward-compat test is vacuous once the committed baseline "
            "carries agent_type; re-baseline it or drop this assertion"
        )
