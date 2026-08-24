# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Unit tests for the gaia-agent scorecard adapter + perf gate-reader.

Covers hub/agents/gaia/python/packaging/gen_scorecard.py (the harness→payload
adapter for `gaia eval agent --agent-type gaia` runs) and eval_perf_report.py
(the T5 perf gate-reader), against synthetic run scorecards — no LLM, no
backend, no Lemonade.
"""

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
PACKAGING = REPO_ROOT / "hub" / "agents" / "gaia" / "python" / "packaging"


def _load(name: str):
    path = PACKAGING / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"gaia_{name}", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _scenario(sid, category, status, score, elapsed=10.0, perf=None, turns=None):
    return {
        "scenario_id": sid,
        "category": category,
        "status": status,
        "overall_score": score,
        "elapsed_s": elapsed,
        "performance_summary": perf,
        "turns": turns or [],
    }


def _run_scorecard(scenarios, agent_type="gaia", model="claude-sonnet-4-6"):
    """Build a synthetic `gaia eval agent` scorecard via the REAL aggregator.

    Uses gaia.eval.scorecard.build_scorecard so the fixture can never drift
    from the shape the runner actually writes.
    """
    from gaia.eval.scorecard import build_scorecard

    return build_scorecard(
        "test-run",
        scenarios,
        {
            "backend_url": "http://127.0.0.1:4200",
            "model": model,
            "budget_per_scenario_usd": 5.0,
            "agent_type": agent_type,
        },
    )


def _write_run(tmp_path, scorecard) -> Path:
    run_dir = tmp_path / "run"
    run_dir.mkdir(exist_ok=True)
    (run_dir / "scorecard.json").write_text(json.dumps(scorecard), encoding="utf-8")
    return run_dir


_BUILD_KWARGS = {
    "model": "Gemma-4-E4B-it-GGUF",
    "ctx_size": 65536,
    "hardware": "AMD Ryzen AI MAX+ (Strix Halo)",
}


class TestGenScorecardPayload:
    def _default_scenarios(self):
        return [
            _scenario("a1", "gaia_core", "PASS", 9.0),
            _scenario("a2", "gaia_core", "FAIL", 3.0),
            _scenario("b1", "gaia_web", "PASS", 8.0),
            _scenario("b2", "gaia_web", "PASS", 8.5),
        ]

    def test_aggregate_is_100x_judged_pass_rate(self, tmp_path):
        mod = _load("gen_scorecard")
        run_dir = _write_run(tmp_path, _run_scorecard(self._default_scenarios()))
        payload = mod.build_payload(run_dir, **_BUILD_KWARGS)

        from gaia.eval.release_scorecard import parse_scorecard, render_scorecard

        parsed = parse_scorecard(render_scorecard(payload))
        # 3 of 4 judged passed
        assert parsed["aggregate"]["value"] == pytest.approx(75.0)
        assert parsed["aggregate"]["name"] == "judged_pass_rate"

    def test_secondaries_are_displayed_weight_zero(self, tmp_path):
        mod = _load("gen_scorecard")
        run_dir = _write_run(tmp_path, _run_scorecard(self._default_scenarios()))
        payload = mod.build_payload(run_dir, **_BUILD_KWARGS)

        by_name = {m["name"]: m for m in payload.metrics}
        assert by_name["judged_pass_rate"]["weight"] == 1.0
        for name in (
            "avg_score_normalized",
            "gaia_core_pass_rate",
            "gaia_web_pass_rate",
        ):
            assert by_name[name]["weight"] == 0.0
        assert by_name["gaia_core_pass_rate"]["value"] == pytest.approx(0.5)
        assert by_name["gaia_web_pass_rate"]["value"] == pytest.approx(1.0)
        # avg_score is 0-10 in the run scorecard; the card shows it in [0,1].
        assert 0.0 <= by_name["avg_score_normalized"]["value"] <= 1.0

    def test_blocked_counts_as_judged(self, tmp_path):
        mod = _load("gen_scorecard")
        scenarios = [
            _scenario("a1", "gaia_core", "PASS", 9.0),
            _scenario("a2", "gaia_core", "BLOCKED_BY_ARCHITECTURE", 2.0),
        ]
        run_dir = _write_run(tmp_path, _run_scorecard(scenarios))
        payload = mod.build_payload(run_dir, **_BUILD_KWARGS)
        assert payload.metrics[0]["value"] == pytest.approx(0.5)

    def test_zero_judged_raises(self, tmp_path):
        mod = _load("gen_scorecard")
        scenarios = [
            _scenario("a1", "gaia_core", "INFRA_ERROR", None),
            _scenario("a2", "gaia_core", "TIMEOUT", None),
        ]
        run_dir = _write_run(tmp_path, _run_scorecard(scenarios))
        with pytest.raises(ValueError, match="Zero judged"):
            mod.build_payload(run_dir, **_BUILD_KWARGS)

    def test_wrong_agent_type_raises(self, tmp_path):
        mod = _load("gen_scorecard")
        run_dir = _write_run(
            tmp_path,
            _run_scorecard(self._default_scenarios(), agent_type=None),
        )
        with pytest.raises(ValueError, match="agent_type"):
            mod.build_payload(run_dir, **_BUILD_KWARGS)

    def test_missing_eval_model_raises(self, tmp_path):
        mod = _load("gen_scorecard")
        run_dir = _write_run(
            tmp_path, _run_scorecard(self._default_scenarios(), model=None)
        )
        with pytest.raises(ValueError, match="config.model"):
            mod.build_payload(run_dir, **_BUILD_KWARGS)

    def test_zero_judged_category_raises(self, tmp_path):
        mod = _load("gen_scorecard")
        scenarios = self._default_scenarios() + [
            _scenario("c1", "gaia_rag", "INFRA_ERROR", None)
        ]
        run_dir = _write_run(tmp_path, _run_scorecard(scenarios))
        with pytest.raises(ValueError, match="gaia_rag"):
            mod.build_payload(run_dir, **_BUILD_KWARGS)

    def test_missing_scorecard_raises_filenotfound(self, tmp_path):
        mod = _load("gen_scorecard")
        empty = tmp_path / "empty"
        empty.mkdir()
        with pytest.raises(FileNotFoundError):
            mod.build_payload(empty, **_BUILD_KWARGS)

    def test_benchmark_scorecard_rejected(self, tmp_path):
        # A `gaia eval benchmark` output (scenarios but no summary) must not
        # silently build a flagship card.
        mod = _load("gen_scorecard")
        run_dir = tmp_path / "run"
        run_dir.mkdir()
        (run_dir / "scorecard.json").write_text(
            json.dumps({"scenarios": []}), encoding="utf-8"
        )
        with pytest.raises(ValueError, match="not a 'gaia eval agent' scorecard"):
            mod.build_payload(run_dir, **_BUILD_KWARGS)

    def test_environment_stamped_and_ctx_readable_by_gate(self, tmp_path):
        mod = _load("gen_scorecard")
        run_dir = _write_run(tmp_path, _run_scorecard(self._default_scenarios()))
        env = {
            "gaia_commit": "abc1234",
            "model": "Gemma-4-E4B-it-GGUF",
            "ctx_size": 65536,
            "hardware": "AMD Ryzen AI MAX+ (Strix Halo)",
            "eval_model": "claude-sonnet-4-6",
        }
        payload = mod.build_payload(run_dir, environment=env, **_BUILD_KWARGS)

        from gaia.eval.release_scorecard import parse_scorecard, render_scorecard
        from gaia.eval.scorecard_gate import env_ctx_size

        parsed = parse_scorecard(render_scorecard(payload))
        assert parsed["recipe"]["environment"]["gaia_commit"] == "abc1234"
        assert parsed["recipe"]["environment"]["eval_model"] == "claude-sonnet-4-6"
        assert env_ctx_size(parsed) == 65536

    def test_no_perf_section_for_run_without_counters(self, tmp_path):
        # Claude-backed harness validation: no Lemonade counters — the perf
        # section must be ABSENT, never zeros.
        mod = _load("gen_scorecard")
        run_dir = _write_run(tmp_path, _run_scorecard(self._default_scenarios()))
        payload = mod.build_payload(run_dir, **_BUILD_KWARGS)
        assert payload.performance is None

    def test_perf_counters_surface_when_present(self, tmp_path):
        mod = _load("gen_scorecard")
        scenarios = [
            _scenario(
                "a1",
                "gaia_core",
                "PASS",
                9.0,
                perf={
                    "avg_tokens_per_second": 25.0,
                    "avg_time_to_first_token": 0.4,
                    "total_input_tokens": 1000,
                    "total_output_tokens": 200,
                },
            )
        ]
        run_dir = _write_run(tmp_path, _run_scorecard(scenarios))
        payload = mod.build_payload(run_dir, **_BUILD_KWARGS)
        assert payload.performance["throughput_tps"] == 25.0
        assert payload.performance["total_input_tokens"] == 1000

    def test_judged_statuses_pinned_to_harness(self):
        # The adapter never imports the runner, so its judged-status literal
        # must be pinned against the harness constant it mirrors.
        from gaia.eval.scorecard import _JUDGED_STATUSES

        assert _JUDGED_STATUSES == {"PASS", "FAIL", "BLOCKED_BY_ARCHITECTURE"}
        mod = _load("gen_scorecard")
        assert set(mod._JUDGED_KEYS) == {"passed", "failed", "blocked"}

    def test_rendered_card_validates_and_gates(self, tmp_path):
        """The rendered gaia card passes validate_scorecard + scorecard_gate:
        first adoption passes; a manufactured LOWER re-run is blocked."""
        mod = _load("gen_scorecard")
        from gaia.eval.release_scorecard import (
            parse_scorecard,
            render_scorecard,
            validate_scorecard,
            write_scorecard,
        )
        from gaia.eval.scorecard_gate import main as gate_main

        run_dir = _write_run(tmp_path, _run_scorecard(self._default_scenarios()))
        payload = mod.build_payload(run_dir, **_BUILD_KWARGS)
        card = tmp_path / "SCORECARD.md"
        write_scorecard(payload, card)
        assert validate_scorecard(parse_scorecard(card)) == []

        # First adoption: no baseline → presence-only pass.
        assert gate_main(["--scorecard", str(card)]) == 0

        # Manufactured lower card (2 of 4 pass) vs the 75.0 baseline → blocked.
        worse_scenarios = [
            _scenario("a1", "gaia_core", "PASS", 9.0),
            _scenario("a2", "gaia_core", "FAIL", 3.0),
            _scenario("b1", "gaia_web", "FAIL", 4.0),
            _scenario("b2", "gaia_web", "PASS", 8.5),
        ]
        worse_dir = tmp_path / "worse"
        worse_dir.mkdir()
        (worse_dir / "scorecard.json").write_text(
            json.dumps(_run_scorecard(worse_scenarios)), encoding="utf-8"
        )
        worse_payload = mod.build_payload(worse_dir, **_BUILD_KWARGS)
        worse_card = tmp_path / "WORSE_SCORECARD.md"
        write_scorecard(worse_payload, worse_card)
        assert (
            gate_main(["--scorecard", str(worse_card), "--baseline-file", str(card)])
            == 1
        )

    def test_merged_collection_dir_pools_categories(self, tmp_path):
        # The CI/refresh layout: eval-out/<category>/scorecard.json — one run
        # per category, merged into a single combined card.
        mod = _load("gen_scorecard")
        collect = tmp_path / "eval-out"
        for cat, scenarios in {
            "gaia_core": [
                _scenario("a1", "gaia_core", "PASS", 9.0),
                _scenario("a2", "gaia_core", "FAIL", 3.0),
            ],
            "gaia_web": [
                _scenario("b1", "gaia_web", "PASS", 8.0),
            ],
        }.items():
            (collect / cat).mkdir(parents=True)
            (collect / cat / "scorecard.json").write_text(
                json.dumps(_run_scorecard(scenarios)), encoding="utf-8"
            )
        payload = mod.build_payload(collect, **_BUILD_KWARGS)
        assert payload.metrics[0]["value"] == pytest.approx(2 / 3)
        assert payload.test_cases_run == 3
        cats = {r["category"] for r in payload.breakdown["per_category"]}
        assert cats == {"gaia_core", "gaia_web"}

    def test_merge_rejects_duplicate_category(self, tmp_path):
        mod = _load("gen_scorecard")
        collect = tmp_path / "eval-out"
        for name in ("run1", "run2"):
            (collect / name).mkdir(parents=True)
            (collect / name / "scorecard.json").write_text(
                json.dumps(_run_scorecard([_scenario("a1", "gaia_core", "PASS", 9.0)])),
                encoding="utf-8",
            )
        with pytest.raises(ValueError, match="more than one scorecard"):
            mod.build_payload(collect, **_BUILD_KWARGS)

    def test_merge_rejects_config_mismatch(self, tmp_path):
        mod = _load("gen_scorecard")
        collect = tmp_path / "eval-out"
        (collect / "run1").mkdir(parents=True)
        (collect / "run1" / "scorecard.json").write_text(
            json.dumps(_run_scorecard([_scenario("a1", "gaia_core", "PASS", 9.0)])),
            encoding="utf-8",
        )
        (collect / "run2").mkdir(parents=True)
        (collect / "run2" / "scorecard.json").write_text(
            json.dumps(
                _run_scorecard(
                    [_scenario("b1", "gaia_web", "PASS", 8.0)],
                    model="claude-opus-5",
                )
            ),
            encoding="utf-8",
        )
        with pytest.raises(ValueError, match="configs\\s+disagree|configs disagree"):
            mod.build_payload(collect, **_BUILD_KWARGS)

    def test_main_end_to_end_writes_card(self, tmp_path):
        run_dir = _write_run(tmp_path, _run_scorecard(self._default_scenarios()))
        out_dir = tmp_path / "out"
        result = subprocess.run(
            [
                sys.executable,
                str(PACKAGING / "gen_scorecard.py"),
                "--run-dir",
                str(run_dir),
                "--model",
                "claude-haiku-4-5",
                "--ctx-size",
                "200000",
                "--hardware",
                "developer workstation (harness validation)",
                "--output-dir",
                str(out_dir),
                "--note",
                "harness validation",
            ],
            capture_output=True,
            text=True,
            cwd=str(REPO_ROOT),
        )
        assert result.returncode == 0, result.stderr
        card = out_dir / "SCORECARD.md"
        assert card.exists()

        from gaia.eval.release_scorecard import parse_scorecard

        parsed = parse_scorecard(card)
        assert parsed["recipe"]["environment"]["note"] == "harness validation"
        assert parsed["recipe"]["environment"]["gaia_commit"]
        assert "lemonade_version" not in parsed["recipe"]["environment"]


class TestPerfReport:
    def _write_cards(self, tmp_path, scenarios):
        out = tmp_path / "eval-out" / "gaia_core"
        out.mkdir(parents=True)
        (out / "scorecard.json").write_text(
            json.dumps(_run_scorecard(scenarios)), encoding="utf-8"
        )
        return tmp_path / "eval-out"

    def test_elapsed_breach_detected(self, tmp_path):
        mod = _load("eval_perf_report")
        scenarios = [
            _scenario("a1", "gaia_core", "PASS", 9.0, elapsed=9999.0),
            _scenario("a2", "gaia_core", "PASS", 9.0, elapsed=5.0),
        ]
        observed, _ = mod.measure(scenarios)
        gate = mod.evaluate({"enforce": False, "max_elapsed_s": 600.0}, *[observed, []])
        assert not gate["passed"]
        assert gate["should_fail"] is False  # report mode
        assert any("a1" in b for b in gate["breaches"])

    def test_min_direction_bar(self, tmp_path):
        mod = _load("eval_perf_report")
        observed = {"min_cache_hit_ratio": {"value": 0.05, "scenario": "a1"}}
        gate = mod.evaluate(
            {"enforce": True, "min_cache_hit_ratio": 0.20}, observed, []
        )
        assert not gate["passed"]
        assert gate["should_fail"] is True

    def test_unmeasured_gated_metric_reported_and_fails_when_enforced(self):
        mod = _load("eval_perf_report")
        gate = mod.evaluate(
            {"enforce": True, "min_cache_hit_ratio": 0.20},
            {},
            ["min_cache_hit_ratio"],
        )
        assert gate["not_measured"] == ["min_cache_hit_ratio"]
        assert gate["should_fail"] is True
        # Report mode: recorded but never blocking.
        gate = mod.evaluate(
            {"enforce": False, "min_cache_hit_ratio": 0.20},
            {},
            ["min_cache_hit_ratio"],
        )
        assert gate["should_fail"] is False

    def test_measure_reports_cache_and_llm_calls_unmeasured(self):
        mod = _load("eval_perf_report")
        scenarios = [
            _scenario(
                "a1",
                "gaia_core",
                "PASS",
                9.0,
                perf={"total_input_tokens": 100, "total_output_tokens": 10},
                turns=[{"turn": 1, "agent_tools": ["read_file", "run_shell_command"]}],
            )
        ]
        observed, not_measured = mod.measure(scenarios)
        assert observed["max_tool_calls_per_turn"]["value"] == 2.0
        assert observed["max_input_tokens_per_scenario"]["value"] == 100.0
        assert "min_cache_hit_ratio" in not_measured
        assert "max_llm_calls_per_turn" in not_measured

    def test_committed_manifest_loads_and_ships_report_mode(self):
        mod = _load("eval_perf_report")
        thresholds = mod.load_thresholds()
        # Report mode until the first runner baseline (plan phase 4) — flipping
        # this to true is a deliberate data change, not a side effect.
        assert thresholds["enforce"] is False

    def test_main_end_to_end_report_json(self, tmp_path, monkeypatch):
        scenarios = [
            _scenario("a1", "gaia_core", "PASS", 9.0, elapsed=12.0),
        ]
        eval_out = self._write_cards(tmp_path, scenarios)
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("GAIA_EVAL_SCORECARDS_DIR", str(eval_out))
        mod = _load("eval_perf_report")
        assert mod.main() == 0
        report = json.loads(
            (tmp_path / "eval-out" / "perf_gate_report.json").read_text(
                encoding="utf-8"
            )
        )
        assert report["perf_gate"]["passed"] is True
        assert report["scenario_count"] == 1

    def test_main_fails_when_nothing_collected(self, tmp_path, monkeypatch):
        (tmp_path / "eval-out").mkdir()
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("GAIA_EVAL_SCORECARDS_DIR", str(tmp_path / "eval-out"))
        mod = _load("eval_perf_report")
        assert mod.main() == 1
