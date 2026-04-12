"""
Milestone 3 — Pipeline Agent Integration Tests.

Tests for the 5 pipeline stage agents:
- domain-analyzer (Stage 1)
- workflow-modeler (Stage 2)
- loom-builder (Stage 3)
- gap-detector (Stage 4)
- pipeline-executor (Stage 5)

Exit criteria for Milestone 3:
1. All 5 pipeline agents load through _load_md_agent() without errors
2. Each agent has non-empty system_prompt (>50 chars)
3. Each agent has valid complexity_range tuple
4. Each agent has at least 3 capabilities
5. Tool-call blocks conform to Section 4 syntax (CALL:, purpose:, IF:/END IF:)
6. Pipeline agent chain is internally consistent (producer/consumer references match)
"""

import asyncio
import re
from pathlib import Path

import pytest

from gaia.agents.registry import AgentRegistry


AGENTS_DIR = Path(r"C:\Users\antmi\gaia\config\agents")

PIPELINE_AGENT_IDS = [
    "domain-analyzer",
    "workflow-modeler",
    "loom-builder",
    "gap-detector",
    "pipeline-executor",
]


@pytest.fixture(scope="module")
def loaded_registry():
    """Load the real agent registry once for all tests."""
    registry = AgentRegistry(agents_dir=AGENTS_DIR, auto_reload=False)
    asyncio.get_event_loop().run_until_complete(registry._load_all_agents())
    return registry


class TestPipelineAgentLoading:
    """Verify all 5 pipeline stage agents load correctly."""

    @pytest.mark.parametrize("agent_id", PIPELINE_AGENT_IDS)
    def test_pipeline_agent_loaded(self, loaded_registry, agent_id):
        """Each pipeline agent must be loaded into the registry."""
        assert agent_id in loaded_registry._agents, f"{agent_id} not loaded"

    @pytest.mark.parametrize("agent_id", PIPELINE_AGENT_IDS)
    def test_pipeline_agent_has_system_prompt(self, loaded_registry, agent_id):
        """Each pipeline agent must have a substantial system prompt (>50 chars)."""
        agent = loaded_registry._agents[agent_id]
        assert len(agent.system_prompt) > 50, (
            f"{agent_id} system_prompt too short: {len(agent.system_prompt)} chars"
        )

    @pytest.mark.parametrize("agent_id", PIPELINE_AGENT_IDS)
    def test_pipeline_agent_complexity_range(self, loaded_registry, agent_id):
        """Each pipeline agent must have a valid complexity_range tuple."""
        agent = loaded_registry._agents[agent_id]
        cr = agent.triggers.complexity_range
        assert isinstance(cr, tuple), f"{agent_id} complexity_range is not tuple: {type(cr)}"
        assert len(cr) == 2, f"{agent_id} complexity_range length != 2: {cr}"
        assert 0.0 <= cr[0] <= cr[1] <= 1.0, f"{agent_id} invalid range: {cr}"

    @pytest.mark.parametrize("agent_id", PIPELINE_AGENT_IDS)
    def test_pipeline_agent_has_capabilities(self, loaded_registry, agent_id):
        """Each pipeline agent must have at least 3 capabilities."""
        agent = loaded_registry._agents[agent_id]
        caps = agent.capabilities.capabilities
        assert len(caps) >= 3, f"{agent_id} has only {len(caps)} capabilities (need >= 3)"

    @pytest.mark.parametrize("agent_id", PIPELINE_AGENT_IDS)
    def test_pipeline_agent_has_tools(self, loaded_registry, agent_id):
        """Each pipeline agent must have at least 2 tools defined."""
        agent = loaded_registry._agents[agent_id]
        assert len(agent.tools) >= 2, f"{agent_id} has only {len(agent.tools)} tools"

    @pytest.mark.parametrize("agent_id", PIPELINE_AGENT_IDS)
    def test_pipeline_agent_enabled(self, loaded_registry, agent_id):
        """All pipeline agents should be enabled."""
        agent = loaded_registry._agents[agent_id]
        assert agent.enabled, f"{agent_id} is disabled"


class TestPipelineAgentToolCallSyntax:
    """Verify tool-call blocks conform to Section 4 of the design spec."""

    @pytest.mark.parametrize("agent_id", PIPELINE_AGENT_IDS)
    def test_tool_call_blocks_exist(self, agent_id):
        """Each pipeline agent must have at least one tool-call block."""
        md_file = AGENTS_DIR / f"{agent_id}.md"
        content = md_file.read_text(encoding="utf-8")
        tool_calls = re.findall(r"```tool-call\n(.*?)```", content, re.DOTALL)
        assert len(tool_calls) >= 1, f"{agent_id} has no tool-call blocks"

    @pytest.mark.parametrize("agent_id", PIPELINE_AGENT_IDS)
    def test_tool_call_has_call_keyword(self, agent_id):
        """Every tool-call block must contain a CALL: line."""
        md_file = AGENTS_DIR / f"{agent_id}.md"
        content = md_file.read_text(encoding="utf-8")
        tool_calls = re.findall(r"```tool-call\n(.*?)```", content, re.DOTALL)
        for i, block in enumerate(tool_calls):
            assert any(l.strip().startswith("CALL:") for l in block.split("\n")), (
                f"{agent_id} block {i}: missing CALL:"
            )

    @pytest.mark.parametrize("agent_id", PIPELINE_AGENT_IDS)
    def test_tool_call_has_purpose(self, agent_id):
        """Every tool-call block must contain a purpose: line."""
        md_file = AGENTS_DIR / f"{agent_id}.md"
        content = md_file.read_text(encoding="utf-8")
        tool_calls = re.findall(r"```tool-call\n(.*?)```", content, re.DOTALL)
        for i, block in enumerate(tool_calls):
            assert any(l.strip().startswith("purpose:") for l in block.split("\n")), (
                f"{agent_id} block {i}: missing purpose:"
            )

    @pytest.mark.parametrize("agent_id", PIPELINE_AGENT_IDS)
    def test_if_endif_pairing(self, agent_id):
        """IF: and END IF: must be properly paired."""
        md_file = AGENTS_DIR / f"{agent_id}.md"
        content = md_file.read_text(encoding="utf-8")
        tool_calls = re.findall(r"```tool-call\n(.*?)```", content, re.DOTALL)
        for i, block in enumerate(tool_calls):
            lines = block.strip().split("\n")
            has_if = any(l.strip().startswith("IF:") for l in lines)
            has_endif = any(l.strip().startswith("END IF") for l in lines)
            assert has_if == has_endif, (
                f"{agent_id} block {i}: IF/END IF mismatch (IF={has_if}, END IF={has_endif})"
            )

    def test_gap_detector_conditional_trigger(self):
        """Gap detector must have IF: gaps_identified pattern for conditional generation."""
        md_file = AGENTS_DIR / "gap-detector.md"
        content = md_file.read_text(encoding="utf-8")
        assert "IF:" in content, "gap-detector missing IF: conditional"
        assert "END IF" in content, "gap-detector missing END IF: closing"


class TestPipelineAgentChainConsistency:
    """Verify producer/consumer references are internally consistent."""

    def test_stage_1_producer_referenced_by_stage_2(self, loaded_registry):
        """Workflow Modeler must reference DomainAnalyzer as its producer."""
        agent = loaded_registry._agents["workflow-modeler"]
        prompt = agent.system_prompt.lower()
        assert "domainanalyzer" in prompt or "domain analyzer" in prompt or "domain_blueprint" in prompt, (
            "workflow-modeler does not reference DomainAnalyzer"
        )

    def test_stage_2_producer_referenced_by_stage_3(self, loaded_registry):
        """Loom Builder must reference WorkflowModeler as its producer."""
        agent = loaded_registry._agents["loom-builder"]
        prompt = agent.system_prompt.lower()
        assert "workflowmodeler" in prompt or "workflow modeler" in prompt or "workflow_model" in prompt, (
            "loom-builder does not reference WorkflowModeler"
        )

    def test_stage_3_producer_referenced_by_stage_5(self, loaded_registry):
        """Pipeline Executor must reference LoomBuilder as its producer."""
        agent = loaded_registry._agents["pipeline-executor"]
        prompt = agent.system_prompt.lower()
        assert "loombuilder" in prompt or "loom builder" in prompt or "loom_topology" in prompt or "loom topology" in prompt, (
            "pipeline-executor does not reference LoomBuilder"
        )

    def test_stage_4_producer_referenced_by_stage_5(self, loaded_registry):
        """Pipeline Executor must reference GapDetector in its flow."""
        agent = loaded_registry._agents["pipeline-executor"]
        prompt = agent.system_prompt.lower()
        assert "gapdetector" in prompt or "gap detector" in prompt or "gap_detection" in prompt or "gap analysis" in prompt, (
            "pipeline-executor does not reference GapDetector"
        )

    def test_all_pipeline_agents_have_pipeline_tag(self, loaded_registry):
        """All pipeline agents should have 'pipeline' in their metadata tags."""
        for agent_id in PIPELINE_AGENT_IDS:
            agent = loaded_registry._agents[agent_id]
            tags = agent.metadata.get("tags", [])
            assert "pipeline" in tags, f"{agent_id} missing 'pipeline' tag"

    def test_pipeline_agents_have_entrypoint(self, loaded_registry):
        """All pipeline agents should have a pipeline.entrypoint defined."""
        for agent_id in PIPELINE_AGENT_IDS:
            agent = loaded_registry._agents[agent_id]
            # Check via triggers or metadata — entrypoint is stored in the raw data
            # For now, verify the agent file has it
            md_file = AGENTS_DIR / f"{agent_id}.md"
            content = md_file.read_text(encoding="utf-8")
            assert "pipeline.entrypoint:" in content, (
                f"{agent_id} missing pipeline.entrypoint in frontmatter"
            )


class TestTotalAgentCount:
    """Verify the complete agent count."""

    def test_total_agents_loaded(self, loaded_registry):
        """At least 23 agents should be loaded (18 migrated + 5 pipeline)."""
        count = len(loaded_registry._agents)
        assert count >= 23, f"Expected >= 23 agents, got {count}"
