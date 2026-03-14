# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Integration tests for ComputerUseMixin.

Tests:
- Learn and replay using a local HTML form (with mock Playwright bridge)
- Workflow persistence across agent restarts (different SharedAgentState instances)
- Screenshot cleanup when a skill is deleted
- Workflow listing persistence
- Replay with parameter substitution end-to-end
"""

import json
import shutil
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

from gaia.agents.base.computer_use import (
    ComputerUseMixin,
    PlaywrightBridge,
    _extract_domain,
)
from gaia.agents.base.memory_mixin import MemoryMixin
from gaia.agents.base.shared_state import KnowledgeDB, SharedAgentState

# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def clean_singleton():
    """Reset the SharedAgentState singleton between tests."""
    SharedAgentState._instance = None
    if hasattr(SharedAgentState, "_initialized"):
        delattr(SharedAgentState, "_initialized")
    yield
    SharedAgentState._instance = None
    if hasattr(SharedAgentState, "_initialized"):
        delattr(SharedAgentState, "_initialized")


@pytest.fixture(autouse=True)
def clean_tool_registry():
    """Clear tool registry before each test to avoid cross-test pollution."""
    from gaia.agents.base.tools import _TOOL_REGISTRY

    saved = dict(_TOOL_REGISTRY)
    _TOOL_REGISTRY.clear()
    yield
    _TOOL_REGISTRY.clear()
    _TOOL_REGISTRY.update(saved)


class MockBridge:
    """Mock PlaywrightBridge that simulates browser actions and writes screenshot files."""

    def __init__(self, headless=True):
        self.headless = headless
        self._launched = False
        self.actions: List[Dict[str, Any]] = []
        self._screenshot_data = b"\x89PNG_test_screenshot"

    def launch(self, url=None):
        self._launched = True
        self.actions.append({"action": "launch", "url": url})
        result = {"status": "launched", "headless": self.headless}
        if url:
            self.navigate(url)
            result["url"] = url
        return result

    def navigate(self, url):
        self.actions.append({"action": "navigate", "url": url})
        return {"status": "navigated", "url": url}

    def click(self, selector):
        self.actions.append({"action": "click", "selector": selector})
        return {"status": "clicked", "selector": selector}

    def type_text(self, selector, text):
        self.actions.append({"action": "type", "selector": selector, "text": text})
        return {"status": "typed", "selector": selector, "text": text}

    def screenshot(self, save_path):
        self.actions.append({"action": "screenshot", "path": save_path})
        path = Path(save_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(self._screenshot_data)
        return self._screenshot_data

    def snapshot(self):
        self.actions.append({"action": "snapshot"})
        return '<form id="testForm"><input id="title"><textarea id="content"></textarea></form>'

    def close(self):
        self._launched = False
        self.actions.append({"action": "close"})
        return {"status": "closed"}


class _TestAgent(MemoryMixin, ComputerUseMixin):
    """Full agent with both MemoryMixin and ComputerUseMixin for integration testing."""

    def __init__(self, workspace_dir, bridge=None):
        self.init_memory(workspace_dir=workspace_dir)
        skills_dir = Path(workspace_dir) / "skills"
        self.init_computer_use(skills_dir=skills_dir, playwright_bridge=bridge)


def _make_agent(workspace, bridge=None):
    """Create a fresh agent with a new singleton."""
    SharedAgentState._instance = None
    if hasattr(SharedAgentState, "_initialized"):
        delattr(SharedAgentState, "_initialized")
    return _TestAgent(workspace_dir=workspace, bridge=bridge or MockBridge())


@pytest.fixture
def workspace(tmp_path):
    """Create a persistent workspace."""
    ws = tmp_path / "workspace"
    ws.mkdir()
    return ws


@pytest.fixture
def form_url():
    """URL for the test form (file:// URL)."""
    form_path = Path(__file__).parent.parent / "fixtures" / "test_form.html"
    return f"file://{form_path.as_posix()}"


# ── Learn and Replay (End-to-End) ────────────────────────────────────────────


class TestLearnAndReplay:
    """Full learn → replay cycle using mock browser."""

    def test_learn_and_replay_html_form(self, workspace, form_url):
        """Learn a form-filling workflow, then replay with different values."""
        bridge = MockBridge()
        agent = _make_agent(workspace, bridge)

        # Define steps to fill the test form
        steps = [
            {
                "action": "click",
                "target": "#title",
                "value": None,
                "notes": "Click title field",
            },
            {
                "action": "type",
                "target": "#title",
                "value": "{title}",
                "notes": "Type title",
            },
            {
                "action": "type",
                "target": "#content",
                "value": "{content}",
                "notes": "Type content",
            },
            {
                "action": "click",
                "target": "#submitBtn",
                "value": None,
                "notes": "Click submit",
            },
        ]

        # Learn the workflow
        result = agent._learn_workflow_impl(
            task_description="Fill test form",
            start_url=form_url,
            steps_json=json.dumps(steps),
        )

        assert result["status"] == "learned"
        assert result["step_count"] == 5  # navigate + 4 user steps
        assert "title" in result["parameters"]
        assert "content" in result["parameters"]
        skill_id = result["skill_id"]

        # Verify skill is in KnowledgeDB
        results = agent.knowledge.recall(query="Fill test form", category="skill")
        assert len(results) >= 1

        # Verify screenshots were created
        skill_dir = agent.skills_dir / skill_id
        assert skill_dir.exists()

        # Now replay with different parameters
        bridge2 = MockBridge()
        agent._playwright_bridge = bridge2

        replay_result = agent._replay_workflow_impl(
            skill_name="Fill test form",
            parameters_json=json.dumps({"title": "My Title", "content": "My Content"}),
            headless=True,
        )

        assert replay_result["status"] == "success"
        assert replay_result["steps_executed"] == 5

        # Verify parameter substitution happened in bridge actions
        type_actions = [a for a in bridge2.actions if a.get("action") == "type"]
        typed_texts = [a["text"] for a in type_actions]
        assert "My Title" in typed_texts
        assert "My Content" in typed_texts

        agent._shared_state.memory.close()
        agent._shared_state.knowledge.close()

    def test_learn_workflow_with_replay_different_params(self, workspace, form_url):
        """Replay the same workflow with multiple different parameter sets."""
        bridge = MockBridge()
        agent = _make_agent(workspace, bridge)

        steps = [
            {"action": "type", "target": "#title", "value": "{title}", "notes": ""},
        ]

        result = agent._learn_workflow_impl(
            task_description="Simple type workflow",
            start_url=form_url,
            steps_json=json.dumps(steps),
        )
        assert result["status"] == "learned"

        # Replay with params set A
        bridge_a = MockBridge()
        agent._playwright_bridge = bridge_a
        res_a = agent._replay_workflow_impl(
            skill_name="Simple type workflow",
            parameters_json=json.dumps({"title": "AAA"}),
        )
        assert res_a["status"] == "success"

        # Replay with params set B
        bridge_b = MockBridge()
        agent._playwright_bridge = bridge_b
        res_b = agent._replay_workflow_impl(
            skill_name="Simple type workflow",
            parameters_json=json.dumps({"title": "BBB"}),
        )
        assert res_b["status"] == "success"

        # Verify different substitutions
        typed_a = [a["text"] for a in bridge_a.actions if a.get("action") == "type"]
        typed_b = [a["text"] for a in bridge_b.actions if a.get("action") == "type"]
        assert "AAA" in typed_a
        assert "BBB" in typed_b

        agent._shared_state.memory.close()
        agent._shared_state.knowledge.close()


# ── Workflow Persistence Across Sessions ─────────────────────────────────────


class TestWorkflowPersistence:
    """Workflows persist across agent restarts."""

    def test_workflow_persists_across_sessions(self, workspace, form_url):
        """Learn workflow -> destroy agent -> create new agent -> workflow is listed."""
        bridge1 = MockBridge()
        agent1 = _make_agent(workspace, bridge1)

        steps = [
            {"action": "click", "target": "#submitBtn", "notes": "Click submit"},
        ]

        result = agent1._learn_workflow_impl(
            task_description="Submit form workflow",
            start_url=form_url,
            steps_json=json.dumps(steps),
        )
        assert result["status"] == "learned"

        agent1._shared_state.memory.close()
        agent1._shared_state.knowledge.close()

        # Create a new agent pointing at the same workspace
        bridge2 = MockBridge()
        agent2 = _make_agent(workspace, bridge2)

        # Workflow should be listed
        listing = agent2._list_workflows_impl()
        assert listing["count"] >= 1
        names = [w["name"] for w in listing["workflows"]]
        assert "Submit form workflow" in names

        # Replay should work
        replay = agent2._replay_workflow_impl(
            skill_name="Submit form workflow",
            parameters_json="{}",
        )
        assert replay["status"] == "success"

        agent2._shared_state.memory.close()
        agent2._shared_state.knowledge.close()

    def test_multiple_workflows_persist(self, workspace, form_url):
        """Multiple workflows all persist across restart."""
        agent1 = _make_agent(workspace, MockBridge())

        for name in ["workflow-alpha", "workflow-beta", "workflow-gamma"]:
            steps = [{"action": "click", "target": "#submitBtn", "notes": name}]
            result = agent1._learn_workflow_impl(
                task_description=name,
                start_url=form_url,
                steps_json=json.dumps(steps),
            )
            assert result["status"] == "learned"

        agent1._shared_state.memory.close()
        agent1._shared_state.knowledge.close()

        agent2 = _make_agent(workspace, MockBridge())
        listing = agent2._list_workflows_impl()
        assert listing["count"] == 3
        names = {w["name"] for w in listing["workflows"]}
        assert names == {"workflow-alpha", "workflow-beta", "workflow-gamma"}

        agent2._shared_state.memory.close()
        agent2._shared_state.knowledge.close()


# ── Screenshot Cleanup ───────────────────────────────────────────────────────


class TestScreenshotCleanup:
    """Deleting a skill removes its screenshot directory."""

    def test_screenshot_cleanup_on_delete(self, workspace, form_url):
        """When a skill is deleted, its screenshot directory is removed."""
        bridge = MockBridge()
        agent = _make_agent(workspace, bridge)

        steps = [
            {"action": "click", "target": "#title", "notes": "Click title"},
        ]

        result = agent._learn_workflow_impl(
            task_description="Deletable workflow",
            start_url=form_url,
            steps_json=json.dumps(steps),
        )
        assert result["status"] == "learned"
        skill_id = result["skill_id"]

        # Verify screenshots exist
        skill_dir = agent.skills_dir / skill_id
        assert skill_dir.exists()
        assert any(skill_dir.iterdir())

        # Delete the workflow
        del_result = agent.delete_workflow("Deletable workflow")
        assert del_result["status"] == "deleted"
        assert del_result["screenshots_removed"] is True

        # Verify screenshot directory is gone
        assert not skill_dir.exists()

        # Verify skill is gone from KnowledgeDB
        listing = agent._list_workflows_impl()
        names = [w["name"] for w in listing["workflows"]]
        assert "Deletable workflow" not in names

        agent._shared_state.memory.close()
        agent._shared_state.knowledge.close()


# ── Usage Recording Persistence ──────────────────────────────────────────────


class TestUsageRecording:
    """Replay success/failure counts persist across sessions."""

    def test_replay_usage_persists(self, workspace, form_url):
        """Successful replays increment use_count, which persists."""
        bridge = MockBridge()
        agent1 = _make_agent(workspace, bridge)

        steps = [{"action": "click", "target": "#submitBtn", "notes": "Submit"}]
        result = agent1._learn_workflow_impl(
            task_description="Usage tracking workflow",
            start_url=form_url,
            steps_json=json.dumps(steps),
        )
        assert result["status"] == "learned"

        # Replay twice
        for _ in range(2):
            agent1._playwright_bridge = MockBridge()
            res = agent1._replay_workflow_impl(
                skill_name="Usage tracking workflow",
                parameters_json="{}",
            )
            assert res["status"] == "success"

        agent1._shared_state.memory.close()
        agent1._shared_state.knowledge.close()

        # New agent should see the usage counts
        agent2 = _make_agent(workspace, MockBridge())
        listing = agent2._list_workflows_impl()
        workflow = next(
            w for w in listing["workflows"] if w["name"] == "Usage tracking workflow"
        )
        assert workflow["use_count"] >= 2
        assert workflow["success_count"] >= 2

        agent2._shared_state.memory.close()
        agent2._shared_state.knowledge.close()


# ── Learn Error Paths ────────────────────────────────────────────────────────


class TestLearnErrorPaths:
    """Error handling in learn and replay flows."""

    def test_learn_invalid_json_steps(self, workspace, form_url):
        """Malformed JSON as steps_json returns an error status, not a crash."""
        bridge = MockBridge()
        agent = _make_agent(workspace, bridge)

        result = agent._learn_workflow_impl(
            task_description="Bad JSON workflow",
            start_url=form_url,
            steps_json="{ this is not valid json !!!",
        )

        assert result["status"] == "error"
        assert "Invalid steps JSON" in result["message"]

        agent._shared_state.memory.close()
        agent._shared_state.knowledge.close()

    def test_learn_empty_steps(self, workspace, form_url):
        """An empty steps array produces a workflow with only the navigate step."""
        bridge = MockBridge()
        agent = _make_agent(workspace, bridge)

        result = agent._learn_workflow_impl(
            task_description="Empty steps workflow",
            start_url=form_url,
            steps_json="[]",
        )

        # Should succeed: the navigate step is always recorded
        assert result["status"] == "learned"
        assert result["step_count"] == 1  # Only the initial navigate step
        assert result["parameters"] == []

        agent._shared_state.memory.close()
        agent._shared_state.knowledge.close()

    def test_replay_missing_skill(self, workspace, form_url):
        """Replaying a skill name that does not exist returns an error result."""
        bridge = MockBridge()
        agent = _make_agent(workspace, bridge)

        result = agent._replay_workflow_impl(
            skill_name="Nonexistent skill that was never learned",
            parameters_json="{}",
        )

        assert result["status"] == "error"
        assert "not found" in result["message"].lower()

        agent._shared_state.memory.close()
        agent._shared_state.knowledge.close()


# ── Replay Parameter Edge Cases ──────────────────────────────────────────────


class TestReplayParameterEdgeCases:
    """Edge cases around parameter substitution during replay."""

    def test_replay_with_missing_parameters(self, workspace, form_url):
        """Replay a workflow with {title} param but omit the parameter.

        The placeholder should remain as literal '{title}' in the output
        because _substitute_params only replaces keys present in the dict.
        """
        bridge = MockBridge()
        agent = _make_agent(workspace, bridge)

        steps = [
            {
                "action": "type",
                "target": "#title",
                "value": "{title}",
                "notes": "Type title",
            },
        ]

        result = agent._learn_workflow_impl(
            task_description="Param missing workflow",
            start_url=form_url,
            steps_json=json.dumps(steps),
        )
        assert result["status"] == "learned"
        assert "title" in result["parameters"]

        # Replay WITHOUT providing the "title" parameter
        bridge2 = MockBridge()
        agent._playwright_bridge = bridge2

        replay_result = agent._replay_workflow_impl(
            skill_name="Param missing workflow",
            parameters_json="{}",
            headless=True,
        )

        assert replay_result["status"] == "success"

        # The placeholder should remain un-substituted
        type_actions = [a for a in bridge2.actions if a.get("action") == "type"]
        typed_texts = [a["text"] for a in type_actions]
        assert "{title}" in typed_texts

        agent._shared_state.memory.close()
        agent._shared_state.knowledge.close()

    def test_replay_with_extra_parameters(self, workspace, form_url):
        """Replay a workflow with {title} param but also pass an extra param.

        The extra parameter should be silently ignored and title substituted.
        """
        bridge = MockBridge()
        agent = _make_agent(workspace, bridge)

        steps = [
            {
                "action": "type",
                "target": "#title",
                "value": "{title}",
                "notes": "Type title",
            },
        ]

        result = agent._learn_workflow_impl(
            task_description="Extra params workflow",
            start_url=form_url,
            steps_json=json.dumps(steps),
        )
        assert result["status"] == "learned"

        # Replay with title AND an extra parameter not in the workflow
        bridge2 = MockBridge()
        agent._playwright_bridge = bridge2

        replay_result = agent._replay_workflow_impl(
            skill_name="Extra params workflow",
            parameters_json=json.dumps({"title": "X", "extra": "Y"}),
            headless=True,
        )

        assert replay_result["status"] == "success"

        # Title should be substituted, extra should be silently ignored
        type_actions = [a for a in bridge2.actions if a.get("action") == "type"]
        typed_texts = [a["text"] for a in type_actions]
        assert "X" in typed_texts
        # {extra} was never a placeholder in any step, so "Y" should not appear
        assert "Y" not in typed_texts

        agent._shared_state.memory.close()
        agent._shared_state.knowledge.close()


# ── Workflow Listing Details ─────────────────────────────────────────────────


class TestWorkflowListingDetails:
    """Detailed assertions on workflow listing output."""

    def test_workflow_listing_includes_parameters(self, workspace, form_url):
        """Learn a workflow with parameters, verify listing includes them."""
        bridge = MockBridge()
        agent = _make_agent(workspace, bridge)

        steps = [
            {
                "action": "type",
                "target": "#title",
                "value": "{title}",
                "notes": "Type title",
            },
            {
                "action": "type",
                "target": "#content",
                "value": "{body}",
                "notes": "Type body",
            },
        ]

        result = agent._learn_workflow_impl(
            task_description="Parameterized listing workflow",
            start_url=form_url,
            steps_json=json.dumps(steps),
        )
        assert result["status"] == "learned"

        listing = agent._list_workflows_impl()
        assert listing["count"] >= 1

        workflow = next(
            w
            for w in listing["workflows"]
            if w["name"] == "Parameterized listing workflow"
        )

        # Parameters should be present in the listing
        assert "parameters" in workflow
        assert "title" in workflow["parameters"]
        assert "body" in workflow["parameters"]

        agent._shared_state.memory.close()
        agent._shared_state.knowledge.close()

    def test_empty_workflow_list(self, workspace):
        """A fresh agent with no workflows returns count=0 and empty list."""
        bridge = MockBridge()
        agent = _make_agent(workspace, bridge)

        listing = agent._list_workflows_impl()
        assert listing["count"] == 0
        assert listing["workflows"] == []
        assert listing["status"] == "empty"

        agent._shared_state.memory.close()
        agent._shared_state.knowledge.close()
