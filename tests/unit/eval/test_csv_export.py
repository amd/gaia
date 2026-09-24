# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""CSV export of an eval run — the sheet people actually analyse.

Pins the three properties that make a spreadsheet trustworthy: a stable
append-only column order (so runs from different commits stack), an empty cell
for anything unmeasured (never 0), and a UTF-8 BOM so Excel on Windows opens it
in the right encoding.
"""

import csv
import importlib.util
from pathlib import Path

_MODULE_PATH = (
    Path(__file__).resolve().parents[3]
    / "hub"
    / "agents"
    / "gaia"
    / "python"
    / "packaging"
    / "eval_csv_export.py"
)
_spec = importlib.util.spec_from_file_location("eval_csv_export", _MODULE_PATH)
csv_export = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(csv_export)


def _scorecard(**overrides):
    card = {
        "run_id": "eval-1",
        "timestamp": "2026-08-24T00:00:00+00:00",
        "config": {
            "agent_type": "gaia",
            "model": "claude-sonnet-4-6",
            "backend_url": "http://127.0.0.1:4200",
            "budget_per_scenario_usd": 5.0,
        },
        "summary": {
            "by_category": {"gaia_core": {"passed": 1, "failed": 1, "avg_score": 7.0}}
        },
        "scenarios": [
            {
                "scenario_id": "a",
                "category": "gaia_core",
                "status": "PASS",
                "overall_score": 9.0,
                "elapsed_s": 12.0,
                "turns": [{"scores": {"correctness": 9, "tool_selection": 8}}],
                "performance_summary": {
                    "avg_time_to_first_token": 1.5,
                    "min_time_to_first_token": 1.0,
                    "max_time_to_first_token": 2.0,
                    "total_input_tokens": 100,
                    "turns_measured": 1,
                    "flags": [],
                },
                "tool_usage": {"total_tool_calls": 3, "max_tool_calls_in_a_turn": 2},
            }
        ],
    }
    card.update(overrides)
    return card


class TestHonestCells:
    def test_unmeasured_is_empty_never_zero(self, tmp_path):
        """A TTFT that was never measured and a 0.0s TTFT must not look alike."""
        card = _scorecard(
            scenarios=[
                {
                    "scenario_id": "nostats",
                    "category": "gaia_core",
                    "status": "PASS",
                    "turns": [],
                    "performance_summary": None,
                    "tool_usage": {
                        "total_tool_calls": 1,
                        "max_tool_calls_in_a_turn": 1,
                    },
                }
            ]
        )
        rows = csv_export.scenario_rows(card, "abc1234")
        csv_export.write_csv(tmp_path / "r.csv", csv_export.RESULT_COLUMNS, rows)
        parsed = list(
            csv.DictReader((tmp_path / "r.csv").read_text("utf-8-sig").splitlines())
        )
        assert parsed[0]["ttft_avg_s"] == ""
        assert parsed[0]["tps_avg"] == ""
        # Tool usage IS measured on a no-stats backend and must survive.
        assert parsed[0]["total_tool_calls"] == "1"

    def test_written_with_bom_for_excel(self, tmp_path):
        csv_export.write_csv(tmp_path / "r.csv", csv_export.RESULT_COLUMNS, [])
        assert (tmp_path / "r.csv").read_bytes().startswith(b"\xef\xbb\xbf")


class TestStableSchema:
    def test_config_is_repeated_on_every_row(self):
        """Repetition is what makes PivotTables work and lets runs stack."""
        card = _scorecard()
        card["scenarios"].append(dict(card["scenarios"][0], scenario_id="b"))
        rows = csv_export.scenario_rows(card, "abc1234")
        assert len(rows) == 2
        for row in rows:
            assert row["run_id"] == "eval-1"
            assert row["gaia_commit"] == "abc1234"
            assert row["agent_type"] == "gaia"

    def test_identity_columns_lead_and_order_is_fixed(self):
        assert csv_export.RESULT_COLUMNS[:3] == [
            "run_id",
            "timestamp_utc",
            "gaia_commit",
        ]
        assert len(set(csv_export.RESULT_COLUMNS)) == len(csv_export.RESULT_COLUMNS)

    def test_judge_model_is_not_mislabelled_as_the_agent(self):
        """config.model is the JUDGE; the agent model is a separate column that
        stays blank rather than borrowing the judge's value."""
        rows = csv_export.scenario_rows(_scorecard(), "abc1234")
        assert rows[0]["judge_model"] == "claude-sonnet-4-6"
        assert rows[0]["agent_model"] in (None, "")


class TestKpisAndStability:
    def test_kpis_and_dimension_means_exported(self):
        rows = csv_export.scenario_rows(_scorecard(), "abc1234")
        row = rows[0]
        assert row["ttft_min_s"] == 1.0 and row["ttft_max_s"] == 2.0
        assert row["total_tool_calls"] == 3
        assert row["correctness"] == 9.0

    def test_stability_columns_populate_from_the_block(self):
        card = _scorecard()
        card["scenarios"][0]["stability"] = {
            "runs": 3,
            "judged": 3,
            "pass_count": 2,
            "pass_rate": 0.6667,
            "stability": "flaky",
            "statuses": ["PASS", "FAIL", "PASS"],
        }
        row = csv_export.scenario_rows(card, "abc1234")[0]
        assert row["stability"] == "flaky"
        assert row["attempt_statuses"] == "PASS;FAIL;PASS"

    def test_category_summary_counts_flaky(self):
        card = _scorecard()
        card["scenarios"][0]["stability"] = {"stability": "flaky"}
        rows = csv_export.category_rows(card, "abc1234")
        assert rows[0]["category"] == "gaia_core"
        assert rows[0]["flaky_count"] == 1
        assert rows[0]["pass_rate"] == 0.5
