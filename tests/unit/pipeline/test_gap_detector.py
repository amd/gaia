# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""
Unit tests for GapDetector pipeline stage.

Tests cover:
- Agent scanning from filesystem
- Agent comparison and gap detection
- Gap analysis and generation planning
- Agent generation triggering
"""

import tempfile
from pathlib import Path
from typing import Any, Dict
from unittest.mock import MagicMock, patch

import pytest

from gaia.pipeline.stages.gap_detector import GapDetector


@pytest.fixture
def gap_detector():
    """Create a GapDetector instance for testing."""
    return GapDetector(model_id="test-model", debug=True)


@pytest.fixture
def sample_agent_data():
    """Sample agent data for testing."""
    return {
        "id": "test-agent",
        "name": "Test Agent",
        "capabilities": ["testing", "analysis"],
        "source": "agents/",
    }


@pytest.fixture
def temp_agents_dir():
    """Create a temporary directory with test agent files."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir_path = Path(tmpdir)

        # Create a test MD agent file
        md_agent = tmpdir_path / "test-agent.md"
        md_agent.write_text("""---
id: test-agent
name: Test Agent
capabilities:
  - testing
  - analysis
---

# Test Agent Prompt

You are a test agent.
""")

        # Create another test MD agent file
        md_agent2 = tmpdir_path / "another-agent.md"
        md_agent2.write_text("""---
id: another-agent
name: Another Agent
capabilities:
  - coding
---

# Another Agent Prompt
""")

        yield tmpdir_path


class TestScanAvailableAgents:
    """Tests for scan_available_agents tool."""

    def test_scan_empty_directories(self, gap_detector):
        """Test scanning when directories don't exist."""
        with tempfile.TemporaryDirectory() as tmpdir:
            empty_path = Path(tmpdir) / "nonexistent"
            result = gap_detector._execute_tool(
                "scan_available_agents",
                {
                    "agents_dir": str(empty_path),
                    "claude_agents_dir": str(empty_path / "claude"),
                },
            )

            assert result["total_count"] == 0
            assert result["sources"]["agents/"] == 0
            assert result["sources"][".claude/agents/"] == 0

    def test_scan_md_agents(self, gap_detector, temp_agents_dir):
        """Test scanning MD agent files."""
        result = gap_detector._execute_tool(
            "scan_available_agents",
            {"agents_dir": str(temp_agents_dir), "claude_agents_dir": "/nonexistent"},
        )

        assert result["total_count"] == 2
        assert result["sources"]["agents/"] == 2

        agent_ids = {agent["id"] for agent in result["agents"]}
        assert "test-agent" in agent_ids
        assert "another-agent" in agent_ids

    def test_scan_agents_dir_not_exist(self, gap_detector):
        """Test scanning when agents directory doesn't exist."""
        with tempfile.TemporaryDirectory() as tmpdir:
            result = gap_detector._execute_tool(
                "scan_available_agents",
                {"agents_dir": str(Path(tmpdir) / "missing"), "claude_agents_dir": ""},
            )

            assert result["total_count"] == 0


class TestCompareAgents:
    """Tests for compare_agents tool."""

    def test_all_agents_available(self, gap_detector):
        """Test when all recommended agents are available."""
        available = [
            {"id": "agent-1", "capabilities": ["testing"]},
            {"id": "agent-2", "capabilities": ["coding"]},
            {"id": "agent-3", "capabilities": ["review"]},
        ]
        recommended = ["agent-1", "agent-2", "agent-3"]

        result = gap_detector._execute_tool(
            "compare_agents",
            {"available_agents": available, "recommended_agents": recommended},
        )

        assert result["coverage_rate"] == 1.0
        assert len(result["missing_ids"]) == 0
        assert len(result["covered_ids"]) == 3

    def test_some_agents_missing(self, gap_detector):
        """Test when some recommended agents are missing."""
        available = [
            {"id": "agent-1", "capabilities": ["testing"]},
            {"id": "agent-3", "capabilities": ["review"]},
        ]
        recommended = ["agent-1", "agent-2", "agent-3"]

        result = gap_detector._execute_tool(
            "compare_agents",
            {"available_agents": available, "recommended_agents": recommended},
        )

        assert result["coverage_rate"] == pytest.approx(0.67, rel=0.01)
        assert result["missing_ids"] == ["agent-2"]
        assert len(result["covered_ids"]) == 2

    def test_all_agents_missing(self, gap_detector):
        """Test when no recommended agents are available."""
        available = [{"id": "other-agent", "capabilities": ["misc"]}]
        recommended = ["agent-1", "agent-2"]

        result = gap_detector._execute_tool(
            "compare_agents",
            {"available_agents": available, "recommended_agents": recommended},
        )

        assert result["coverage_rate"] == 0.0
        assert sorted(result["missing_ids"]) == ["agent-1", "agent-2"]

    def test_empty_recommended_list(self, gap_detector):
        """Test with empty recommended agents list."""
        available = [{"id": "agent-1", "capabilities": ["testing"]}]
        recommended = []

        result = gap_detector._execute_tool(
            "compare_agents",
            {"available_agents": available, "recommended_agents": recommended},
        )

        assert result["coverage_rate"] == 1.0
        assert len(result["missing_ids"]) == 0


class TestAnalyzeGaps:
    """Tests for analyze_gaps tool."""

    def test_gaps_identified(self, gap_detector):
        """Test gap analysis when gaps exist."""
        missing = ["agent-1", "agent-2"]
        task_objective = "Build a test framework"

        result = gap_detector._execute_tool(
            "analyze_gaps",
            {"missing_agents": missing, "task_objective": task_objective},
        )

        assert result["gaps_identified"] is True
        assert result["generation_required"] is True
        assert result["can_proceed"] is False
        assert result["generation_plan"]["agents_to_generate"] == missing
        assert result["generation_plan"]["priority"] == "high"

    def test_no_gaps_identified(self, gap_detector):
        """Test gap analysis when no gaps exist."""
        missing = []
        task_objective = "Build a test framework"

        result = gap_detector._execute_tool(
            "analyze_gaps",
            {"missing_agents": missing, "task_objective": task_objective},
        )

        assert result["gaps_identified"] is False
        assert result["generation_required"] is False
        assert result["can_proceed"] is True
        assert result["generation_plan"]["priority"] == "none"

    def test_gap_analysis_state_updated(self, gap_detector):
        """Test that gap analysis state is properly stored."""
        missing = ["agent-1"]
        task_objective = "Test task"

        gap_detector._execute_tool(
            "analyze_gaps",
            {"missing_agents": missing, "task_objective": task_objective},
        )

        # Verify internal state was updated
        assert gap_detector._gap_analysis["gaps_identified"] is True
        assert gap_detector._gap_analysis["missing_agents"] == ["agent-1"]


class TestTriggerAgentGeneration:
    """Tests for trigger_agent_generation tool."""

    def test_generation_triggered_when_gaps_exist(self, gap_detector):
        """Test that generation is triggered when gaps exist."""
        generation_plan = {
            "generation_required": True,
            "agents_to_generate": ["agent-1", "agent-2"],
            "target_domain": "test-domain",
            "priority": "high",
        }

        result = gap_detector._execute_tool(
            "trigger_agent_generation", {"generation_plan": generation_plan}
        )

        assert result["generation_triggered"] is True
        assert result["status"] == "pending"
        assert "mcp__master-ecosystem-creator__spawn_agents" in result["mcp_tool_call"]
        assert "agent-1" in result["mcp_tool_call"]
        assert "agent-2" in result["mcp_tool_call"]

    def test_generation_skipped_when_no_gaps(self, gap_detector):
        """Test that generation is skipped when no gaps exist."""
        generation_plan = {
            "generation_required": False,
            "agents_to_generate": [],
            "target_domain": "test-domain",
        }

        result = gap_detector._execute_tool(
            "trigger_agent_generation", {"generation_plan": generation_plan}
        )

        assert result["generation_triggered"] is False
        assert result["status"] == "skipped"
        assert result["reason"] == "no_gaps_identified"

    def test_mcp_tool_call_format(self, gap_detector):
        """Test that MCP tool call is properly formatted."""
        generation_plan = {
            "generation_required": True,
            "agents_to_generate": ["test-agent"],
            "target_domain": "data-analysis",
            "priority": "high",
        }

        result = gap_detector._execute_tool(
            "trigger_agent_generation", {"generation_plan": generation_plan}
        )

        mcp_call = result["mcp_tool_call"]
        assert "```tool-call" in mcp_call
        assert "CALL: mcp__master-ecosystem-creator__spawn_agents" in mcp_call
        assert "purpose:" in mcp_call
        assert "TARGET_DOMAIN: data-analysis" in mcp_call
        assert "BLOCK_UNTIL_COMPLETE: true" in mcp_call


class TestGetGapAnalysis:
    """Tests for get_gap_analysis tool."""

    def test_get_gap_analysis_empty(self, gap_detector):
        """Test getting gap analysis when not yet set."""
        result = gap_detector._execute_tool("get_gap_analysis", {})

        assert result["gaps_identified"] is False
        assert result["missing_agents"] == []
        assert result["generation_required"] is False
        assert result["can_proceed"] is True

    def test_get_gap_analysis_after_analysis(self, gap_detector):
        """Test getting gap analysis after running analysis."""
        # First run analyze_gaps
        gap_detector._execute_tool(
            "analyze_gaps",
            {"missing_agents": ["agent-1"], "task_objective": "test"},
        )

        # Then get the analysis
        result = gap_detector._execute_tool("get_gap_analysis", {})

        assert result["gaps_identified"] is True
        assert result["missing_agents"] == ["agent-1"]


class TestParseAgentFile:
    """Tests for _parse_agent_file helper method."""

    def test_parse_valid_md_agent(self, gap_detector, temp_agents_dir):
        """Test parsing a valid MD agent file."""
        agent_file = temp_agents_dir / "test-agent.md"
        result = gap_detector._parse_agent_file(agent_file, "agents/")

        assert result is not None
        assert result["id"] == "test-agent"
        assert "testing" in result["capabilities"]
        assert result["source"] == "agents/"

    def test_parse_nonexistent_file(self, gap_detector):
        """Test parsing a file that doesn't exist."""
        result = gap_detector._parse_agent_file(Path("/nonexistent/file.md"), "agents/")
        assert result is None

    def test_parse_invalid_md_no_frontmatter(self, gap_detector, temp_agents_dir):
        """Test parsing MD file without frontmatter."""
        invalid_file = temp_agents_dir / "invalid.md"
        invalid_file.write_text("# No frontmatter here\n\nJust body content.")

        result = gap_detector._parse_agent_file(invalid_file, "agents/")
        # Should return None for invalid file
        assert result is None


class TestGapDetectorIntegration:
    """Integration tests for complete gap detection workflow."""

    def test_full_gap_detection_workflow(self, gap_detector, temp_agents_dir):
        """Test the complete gap detection workflow."""
        # Step 1: Scan available agents
        scan_result = gap_detector._execute_tool(
            "scan_available_agents",
            {"agents_dir": str(temp_agents_dir), "claude_agents_dir": "/nonexistent"},
        )
        assert scan_result["total_count"] == 2

        # Step 2: Compare against recommended agents (some missing)
        recommended = ["test-agent", "missing-agent-1", "missing-agent-2"]
        compare_result = gap_detector._execute_tool(
            "compare_agents",
            {
                "available_agents": scan_result["agents"],
                "recommended_agents": recommended,
            },
        )
        assert len(compare_result["missing_ids"]) == 2
        assert compare_result["coverage_rate"] < 1.0

        # Step 3: Analyze gaps
        analyze_result = gap_detector._execute_tool(
            "analyze_gaps",
            {
                "missing_agents": compare_result["missing_ids"],
                "task_objective": "Test workflow",
            },
        )
        assert analyze_result["gaps_identified"] is True
        assert analyze_result["generation_required"] is True

        # Step 4: Trigger generation - use the generation_plan from analyze_result
        # The trigger tool checks generation_required in the passed plan
        generation_plan = {
            "generation_required": analyze_result["generation_required"],
            "agents_to_generate": analyze_result["missing_agents"],
            "target_domain": "Test workflow",
        }
        trigger_result = gap_detector._execute_tool(
            "trigger_agent_generation",
            {"generation_plan": generation_plan},
        )
        assert trigger_result["generation_triggered"] is True


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
