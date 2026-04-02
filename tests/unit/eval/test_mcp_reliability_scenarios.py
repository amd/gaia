# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Tests that MCP reliability scenario YAML files are valid and discoverable."""

from pathlib import Path

import yaml

from gaia.eval.runner import SCENARIOS_DIR, validate_scenario

MCP_RELIABILITY_DIR = SCENARIOS_DIR / "mcp_reliability"


class TestMCPReliabilityScenarios:
    """Validate all MCP reliability YAML files against the eval schema."""

    def test_scenario_directory_exists(self):
        assert (
            MCP_RELIABILITY_DIR.exists()
        ), f"MCP reliability scenario directory not found: {MCP_RELIABILITY_DIR}"

    def test_minimum_scenario_count(self):
        yamls = list(MCP_RELIABILITY_DIR.rglob("*.yaml"))
        assert (
            len(yamls) >= 8
        ), f"Expected at least 8 MCP reliability scenarios, found {len(yamls)}"

    def test_all_scenarios_validate(self):
        """Each YAML file must pass validate_scenario() without error."""
        yamls = list(MCP_RELIABILITY_DIR.rglob("*.yaml"))
        assert yamls, "No YAML files found in mcp_reliability directory"

        for path in yamls:
            data = yaml.safe_load(path.read_text(encoding="utf-8"))
            # validate_scenario raises RuntimeError on failure
            validate_scenario(path, data)

    def test_all_scenarios_have_mcp_reliability_category(self):
        """Every scenario in the directory must use category: mcp_reliability."""
        for path in MCP_RELIABILITY_DIR.rglob("*.yaml"):
            data = yaml.safe_load(path.read_text(encoding="utf-8"))
            assert data.get("category") == "mcp_reliability", (
                f"{path.name}: expected category 'mcp_reliability', "
                f"got '{data.get('category')}'"
            )

    def test_scenario_ids_are_unique(self):
        """No two scenarios should share the same id."""
        ids = []
        for path in MCP_RELIABILITY_DIR.rglob("*.yaml"):
            data = yaml.safe_load(path.read_text(encoding="utf-8"))
            ids.append(data.get("id"))

        duplicates = [sid for sid in ids if ids.count(sid) > 1]
        assert not duplicates, f"Duplicate scenario IDs found: {set(duplicates)}"

    def test_complexity_tiers_covered(self):
        """Scenarios should cover simple, moderate, and complex tiers via naming."""
        names = [p.stem for p in MCP_RELIABILITY_DIR.rglob("*.yaml")]
        has_simple = any("simple" in n for n in names)
        has_moderate = any("moderate" in n for n in names)
        has_complex = any("complex" in n for n in names)
        assert has_simple, "No simple-tier scenarios found"
        assert has_moderate, "No moderate-tier scenarios found"
        assert has_complex, "No complex-tier scenarios found"

    def test_all_scenarios_have_success_criteria_or_ground_truth(self):
        """Every turn must have success_criteria or ground_truth."""
        for path in MCP_RELIABILITY_DIR.rglob("*.yaml"):
            data = yaml.safe_load(path.read_text(encoding="utf-8"))
            for turn in data.get("turns", []):
                has_gt = turn.get("ground_truth") and isinstance(
                    turn["ground_truth"], dict
                )
                has_sc = turn.get("success_criteria") and isinstance(
                    turn["success_criteria"], str
                )
                assert has_gt or has_sc, (
                    f"{path.name} turn {turn.get('turn')}: "
                    "must have ground_truth (dict) or success_criteria (str)"
                )
