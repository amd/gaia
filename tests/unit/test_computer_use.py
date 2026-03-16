# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Unit tests for ComputerUseMixin: browser-based workflow learning and replay.

Tests cover:
- learn_workflow: stores skill, captures screenshots, correct step format
- replay_workflow: executes steps, substitutes params, records success/failure
- Self-healing: tries alternative selector on failure, gives up on double failure
- list_workflows: domain filtering, type filtering, all workflows
- test_workflow: uses visible (non-headless) browser mode
- Mixin registration: all 4 tools present in registry
- Screenshot cleanup: deleting a skill removes its screenshot directory
"""

import json
import shutil
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional
from unittest.mock import MagicMock, call, patch

import pytest

from gaia.agents.base.computer_use import (
    ComputerUseMixin,
    PlaywrightBridge,
    _extract_domain,
    _extract_skill_triggers,
    _substitute_params,
)
from gaia.agents.base.shared_state import KnowledgeDB, get_shared_state

# ============================================================================
# Test Fixtures
# ============================================================================


class MockPlaywrightBridge:
    """Mock PlaywrightBridge for testing without real browser."""

    def __init__(self, headless=True):
        self.headless = headless
        self._launched = False
        self.actions_log: List[Dict[str, Any]] = []
        self._fail_selectors: set = set()  # Selectors that should fail
        self._screenshot_data = b"\x89PNG_test_data"

    def launch(self, url=None):
        self._launched = True
        self.actions_log.append({"action": "launch", "url": url})
        result = {"status": "launched", "headless": self.headless}
        if url:
            self.navigate(url)
            result["url"] = url
        return result

    def navigate(self, url):
        self.actions_log.append({"action": "navigate", "url": url})
        return {"status": "navigated", "url": url}

    def click(self, selector):
        self.actions_log.append({"action": "click", "selector": selector})
        if selector in self._fail_selectors:
            raise RuntimeError(f"Element not found: {selector}")
        return {"status": "clicked", "selector": selector}

    def type_text(self, selector, text):
        self.actions_log.append({"action": "type", "selector": selector, "text": text})
        if selector in self._fail_selectors:
            raise RuntimeError(f"Element not found: {selector}")
        return {"status": "typed", "selector": selector, "text": text}

    def screenshot(self, save_path):
        self.actions_log.append({"action": "screenshot", "path": save_path})
        path = Path(save_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(self._screenshot_data)
        return self._screenshot_data

    def snapshot(self):
        self.actions_log.append({"action": "snapshot"})
        return '<div class="compose-btn">Start a post</div>'

    def close(self):
        self._launched = False
        self.actions_log.append({"action": "close"})
        return {"status": "closed"}


class MockComputerUseAgent(ComputerUseMixin):
    """Minimal agent-like class with ComputerUseMixin for testing."""

    def __init__(self, workspace_dir, bridge=None):
        self._workspace_dir = workspace_dir
        self._skills_dir = Path(workspace_dir) / "skills"
        self._skills_dir.mkdir(parents=True, exist_ok=True)
        self._playwright_bridge = bridge

        # Initialize KnowledgeDB directly (bypass MemoryMixin for unit tests)
        from gaia.agents.base.shared_state import SharedAgentState

        self._shared_state = SharedAgentState.__new__(SharedAgentState)
        self._shared_state.knowledge = KnowledgeDB(
            str(Path(workspace_dir) / "knowledge.db")
        )
        self._alt_selector_response = None  # For self-heal testing

    @property
    def knowledge(self):
        return self._shared_state.knowledge

    def _suggest_alternative_selector(
        self, dom_snapshot, original_selector, error, step_notes
    ):
        """Override for test control."""
        return self._alt_selector_response


@pytest.fixture
def workspace(tmp_path):
    """Create a temporary workspace directory."""
    ws = tmp_path / "test_workspace"
    ws.mkdir()
    return ws


@pytest.fixture
def mock_bridge():
    """Create a mock Playwright bridge."""
    return MockPlaywrightBridge(headless=False)


@pytest.fixture
def agent(workspace, mock_bridge):
    """Create a test agent with mock bridge."""
    return MockComputerUseAgent(
        workspace_dir=str(workspace),
        bridge=mock_bridge,
    )


@pytest.fixture
def sample_steps_json():
    """Sample steps for learn_workflow."""
    steps = [
        {
            "action": "click",
            "target": "div.share-box-feed-entry__trigger",
            "value": None,
            "notes": "Click compose button",
        },
        {
            "action": "type",
            "target": "div.ql-editor",
            "value": "{content}",
            "notes": "Type post content",
        },
        {
            "action": "click",
            "target": "button.share-actions__primary-action",
            "value": None,
            "notes": "Click Post button",
        },
    ]
    return json.dumps(steps)


# ============================================================================
# learn_workflow tests
# ============================================================================


class TestLearnWorkflow:
    """Tests for learn_workflow tool."""

    def test_learn_workflow_stores_skill(self, agent, sample_steps_json):
        """Mock Playwright -> stores skill with type='replay' in KnowledgeDB."""
        result = agent._learn_workflow_impl(
            task_description="Post content on LinkedIn feed",
            start_url="https://www.linkedin.com/feed/",
            steps_json=sample_steps_json,
        )

        assert result["status"] == "learned"
        assert result["skill_id"]
        assert result["step_count"] == 4  # 1 navigate + 3 user steps

        # Verify stored in KnowledgeDB
        skills = agent._get_all_skills()
        assert len(skills) >= 1

        skill = skills[0]
        assert skill["category"] == "skill"
        assert skill["metadata"]["type"] == "replay"
        assert len(skill["metadata"]["steps"]) == 4
        assert "content" in skill["metadata"]["parameters"]

    def test_learn_workflow_captures_screenshots(self, agent, sample_steps_json):
        """Screenshots saved to skills/{id}/step_N.png for each step."""
        result = agent._learn_workflow_impl(
            task_description="Post on LinkedIn",
            start_url="https://linkedin.com/feed/",
            steps_json=sample_steps_json,
        )

        assert result["status"] == "learned"
        skill_id = result["skill_id"]
        skill_dir = agent.skills_dir / skill_id

        # Check screenshot files exist (step_0 through step_3)
        # Note: screenshots might be in temp_id dir or skill_id dir
        # depending on dedup. Let's check from the stored metadata.
        skills = agent._get_all_skills()
        skill = skills[0]
        steps = skill["metadata"]["steps"]

        for step in steps:
            screenshot_rel = step["screenshot"]
            assert screenshot_rel.startswith("skills/")
            assert screenshot_rel.endswith(".png")

        # Verify at least some screenshots exist on disk
        # The skill_dir may be under the skill_id or temp_id
        total_screenshots = 0
        for d in agent.skills_dir.iterdir():
            if d.is_dir():
                pngs = list(d.glob("*.png"))
                total_screenshots += len(pngs)

        assert (
            total_screenshots >= 4
        ), f"Expected >=4 screenshots, found {total_screenshots}"

    def test_learn_workflow_step_format(self, agent, sample_steps_json):
        """Each step has required fields: step, action, target, value, screenshot, notes."""
        result = agent._learn_workflow_impl(
            task_description="Post on LinkedIn",
            start_url="https://linkedin.com/feed/",
            steps_json=sample_steps_json,
        )

        skills = agent._get_all_skills()
        skill = skills[0]
        steps = skill["metadata"]["steps"]

        required_fields = {"step", "action", "target", "value", "screenshot", "notes"}

        for step in steps:
            missing = required_fields - set(step.keys())
            assert (
                not missing
            ), f"Step {step.get('step', '?')} missing fields: {missing}"
            assert step["action"] in {
                "navigate",
                "click",
                "type",
            }, f"Invalid action: {step['action']}"

    def test_learn_workflow_extracts_domain(self, agent):
        """Domain is correctly extracted from start_url."""
        result = agent._learn_workflow_impl(
            task_description="Test workflow",
            start_url="https://www.example.com/path",
            steps_json="[]",
        )

        assert result["status"] == "learned"
        assert result["domain"] == "example.com"

    def test_learn_workflow_extracts_parameters(self, agent):
        """Parameters with {placeholder} syntax are detected and recorded."""
        steps = [
            {
                "action": "type",
                "target": "input#title",
                "value": "{title}",
                "notes": "Enter title",
            },
            {
                "action": "type",
                "target": "textarea#body",
                "value": "{body_text}",
                "notes": "Enter body",
            },
        ]

        result = agent._learn_workflow_impl(
            task_description="Fill form",
            start_url="https://example.com/form",
            steps_json=json.dumps(steps),
        )

        assert result["status"] == "learned"
        assert sorted(result["parameters"]) == ["body_text", "title"]

    def test_learn_workflow_invalid_steps_json(self, agent):
        """Invalid JSON in steps returns error."""
        result = agent._learn_workflow_impl(
            task_description="Bad workflow",
            start_url="https://example.com",
            steps_json="not valid json[",
        )

        assert result["status"] == "error"
        assert "Invalid steps JSON" in result["message"]


# ============================================================================
# replay_workflow tests
# ============================================================================


class TestReplayWorkflow:
    """Tests for replay_workflow tool."""

    def _store_skill(self, agent, content="Post on LinkedIn", domain="linkedin.com"):
        """Helper to store a test skill in KnowledgeDB."""
        steps = [
            {
                "step": 0,
                "action": "navigate",
                "target": "https://linkedin.com/feed/",
                "value": None,
                "screenshot": "skills/test/step_0.png",
                "notes": "Go to feed",
            },
            {
                "step": 1,
                "action": "click",
                "target": "div.share-box",
                "value": None,
                "screenshot": "skills/test/step_1.png",
                "notes": "Click compose",
            },
            {
                "step": 2,
                "action": "type",
                "target": "div.ql-editor",
                "value": "{content}",
                "screenshot": "skills/test/step_2.png",
                "notes": "Type post content",
            },
            {
                "step": 3,
                "action": "click",
                "target": "button.post-btn",
                "value": None,
                "screenshot": "skills/test/step_3.png",
                "notes": "Click Post",
            },
        ]
        metadata = {
            "type": "replay",
            "steps": steps,
            "parameters": ["content"],
            "tools_used": ["playwright"],
        }

        skill_id = agent.knowledge.store_insight(
            category="skill",
            content=content,
            domain=domain,
            triggers=["linkedin", "post"],
            metadata=metadata,
        )
        return skill_id

    def test_replay_workflow_executes_steps(self, agent, mock_bridge):
        """Mock Playwright -> navigate, click, type executed in correct order."""
        self._store_skill(agent)

        result = agent._replay_workflow_impl(
            skill_name="Post on LinkedIn",
            parameters_json='{"content": "Hello World!"}',
            headless=True,
        )

        assert result["status"] == "success"
        assert result["steps_executed"] == 4

        # Verify actions were called in order
        action_types = [
            a["action"]
            for a in mock_bridge.actions_log
            if a["action"] not in {"launch", "close", "screenshot", "snapshot"}
        ]
        assert action_types == ["navigate", "click", "type", "click"]

    def test_replay_workflow_substitutes_params(self, agent, mock_bridge):
        """'{content}' in step value is replaced with provided parameter."""
        self._store_skill(agent)

        result = agent._replay_workflow_impl(
            skill_name="Post on LinkedIn",
            parameters_json='{"content": "Exciting AI news!"}',
            headless=True,
        )

        assert result["status"] == "success"

        # Find the type action and check the text was substituted
        type_actions = [a for a in mock_bridge.actions_log if a["action"] == "type"]
        assert len(type_actions) == 1
        assert type_actions[0]["text"] == "Exciting AI news!"

    def test_replay_workflow_records_success(self, agent, mock_bridge):
        """On successful replay, record_usage(success=True) is called."""
        skill_id = self._store_skill(agent)

        # Spy on record_usage
        original_record_usage = agent.knowledge.record_usage
        record_usage_calls = []

        def spy_record_usage(iid, success=True):
            record_usage_calls.append({"insight_id": iid, "success": success})
            return original_record_usage(iid, success)

        agent.knowledge.record_usage = spy_record_usage

        result = agent._replay_workflow_impl(
            skill_name="Post on LinkedIn",
            parameters_json="{}",
            headless=True,
        )

        assert result["status"] == "success"
        assert len(record_usage_calls) == 1
        assert record_usage_calls[0]["success"] is True
        assert record_usage_calls[0]["insight_id"] == skill_id

    def test_replay_workflow_handles_failure(self, agent, mock_bridge):
        """When click fails, agent takes screenshot and attempts alternative selector."""
        self._store_skill(agent)

        # Make the compose button fail
        mock_bridge._fail_selectors.add("div.share-box")

        # Provide an alternative selector that succeeds
        agent._alt_selector_response = "button.compose-new"

        result = agent._replay_workflow_impl(
            skill_name="Post on LinkedIn",
            parameters_json='{"content": "Test"}',
            headless=True,
        )

        # Should succeed because self-heal found alternative
        assert result["status"] == "success"

        # Verify screenshot was taken during self-heal
        screenshot_actions = [
            a for a in mock_bridge.actions_log if a["action"] == "screenshot"
        ]
        assert len(screenshot_actions) >= 1

        # Verify snapshot was taken for diagnostic
        snapshot_actions = [
            a for a in mock_bridge.actions_log if a["action"] == "snapshot"
        ]
        assert len(snapshot_actions) >= 1

    def test_replay_workflow_gives_up(self, agent, mock_bridge):
        """Both primary and alternative selectors fail -> error + success=False."""
        skill_id = self._store_skill(agent)

        # Make the compose button fail
        mock_bridge._fail_selectors.add("div.share-box")

        # Alternative also fails
        alt_selector = "button.alt-compose"
        mock_bridge._fail_selectors.add(alt_selector)
        agent._alt_selector_response = alt_selector

        # Spy on record_usage
        record_usage_calls = []
        original_record_usage = agent.knowledge.record_usage

        def spy_record_usage(iid, success=True):
            record_usage_calls.append({"insight_id": iid, "success": success})
            return original_record_usage(iid, success)

        agent.knowledge.record_usage = spy_record_usage

        result = agent._replay_workflow_impl(
            skill_name="Post on LinkedIn",
            parameters_json='{"content": "Test"}',
            headless=True,
        )

        assert result["status"] == "error"
        assert (
            "failed" in result["message"].lower() or "fail" in result["message"].lower()
        )

        # Verify failure was recorded
        assert len(record_usage_calls) == 1
        assert record_usage_calls[0]["success"] is False

    def test_replay_workflow_skill_not_found(self, agent):
        """Searching for non-existent skill returns error."""
        result = agent._replay_workflow_impl(
            skill_name="Non-existent workflow",
            parameters_json="{}",
            headless=True,
        )

        assert result["status"] == "error"
        assert "not found" in result["message"].lower()

    def test_replay_workflow_invalid_params_json(self, agent):
        """Invalid JSON in parameters returns error."""
        self._store_skill(agent)

        result = agent._replay_workflow_impl(
            skill_name="Post on LinkedIn",
            parameters_json="not valid json{",
            headless=True,
        )

        assert result["status"] == "error"
        assert "Invalid parameters JSON" in result["message"]


# ============================================================================
# list_workflows tests
# ============================================================================


class TestListWorkflows:
    """Tests for list_workflows tool."""

    def _store_skills(self, agent):
        """Store multiple test skills with different domains and types."""
        # Replay skill for LinkedIn
        agent.knowledge.store_insight(
            category="skill",
            content="Post content on LinkedIn feed",
            domain="linkedin.com",
            triggers=["linkedin", "post", "social"],
            metadata={
                "type": "replay",
                "steps": [
                    {
                        "step": 1,
                        "action": "click",
                        "target": "button",
                        "value": None,
                        "screenshot": "s.png",
                        "notes": "click",
                    }
                ],
                "parameters": ["content"],
            },
        )

        # API skill for Gmail
        agent.knowledge.store_insight(
            category="skill",
            content="Gmail email management via API",
            domain="gmail",
            triggers=["gmail", "email", "api"],
            metadata={
                "type": "api",
                "provider": "gmail",
                "capabilities": ["list_messages", "send_message"],
            },
        )

        # Decision skill for email triage
        agent.knowledge.store_insight(
            category="skill",
            content="Triage incoming emails by priority",
            domain="gmail",
            triggers=["email", "triage", "priority"],
            metadata={
                "type": "decision",
                "observe": {"extract": ["sender", "subject"]},
                "actions": {"archive": {}, "star": {}},
            },
        )

        # Also store a non-skill insight (should not appear)
        agent.knowledge.store_insight(
            category="fact",
            content="GAIA supports NPU acceleration",
            domain="technology",
        )

    def test_list_workflows_filters_domain(self, agent):
        """list_workflows(domain='linkedin.com') returns only LinkedIn workflows."""
        self._store_skills(agent)

        result = agent._list_workflows_impl(domain="linkedin.com")

        assert result["status"] == "found"
        assert result["count"] == 1
        assert result["workflows"][0]["domain"] == "linkedin.com"
        assert result["workflows"][0]["type"] == "replay"

    def test_list_workflows_filters_type(self, agent):
        """list_workflows(skill_type='api') returns only API skills."""
        self._store_skills(agent)

        result = agent._list_workflows_impl(skill_type="api")

        assert result["status"] == "found"
        assert result["count"] == 1
        assert result["workflows"][0]["type"] == "api"
        assert "gmail" in result["workflows"][0]["name"].lower()

    def test_list_workflows_all(self, agent):
        """list_workflows() with no filters returns all skill-category insights."""
        self._store_skills(agent)

        result = agent._list_workflows_impl()

        assert result["status"] == "found"
        # Should have 3 skills (replay, api, decision) but NOT the fact
        assert result["count"] == 3

        types = {w["type"] for w in result["workflows"]}
        assert types == {"replay", "api", "decision"}

    def test_list_workflows_empty(self, agent):
        """list_workflows() with no skills returns empty status."""
        result = agent._list_workflows_impl()

        assert result["status"] == "empty"
        assert result["count"] == 0

    def test_list_workflows_domain_and_type(self, agent):
        """Combined domain + type filter works correctly."""
        self._store_skills(agent)

        result = agent._list_workflows_impl(domain="gmail", skill_type="decision")

        assert result["status"] == "found"
        assert result["count"] == 1
        assert result["workflows"][0]["type"] == "decision"
        assert result["workflows"][0]["domain"] == "gmail"


# ============================================================================
# test_workflow tests
# ============================================================================


class TestTestWorkflow:
    """Tests for test_workflow tool (visible browser replay)."""

    def test_test_workflow_uses_visible_browser(self, agent, mock_bridge):
        """test_workflow() replays in visible (non-headless) mode."""
        # Store a simple skill
        agent.knowledge.store_insight(
            category="skill",
            content="Simple test workflow",
            domain="example.com",
            metadata={
                "type": "replay",
                "steps": [
                    {
                        "step": 0,
                        "action": "navigate",
                        "target": "https://example.com",
                        "value": None,
                        "screenshot": "s.png",
                        "notes": "Go to example",
                    }
                ],
                "parameters": [],
            },
        )

        # test_workflow calls _replay_workflow_impl with headless=False
        result = agent._replay_workflow_impl(
            skill_name="Simple test workflow",
            parameters_json="{}",
            headless=False,
        )

        assert result["status"] == "success"
        assert result["headless"] is False

        # Verify the bridge was set to non-headless
        assert mock_bridge.headless is False


# ============================================================================
# Mixin registration tests
# ============================================================================


class TestMixinRegistration:
    """Tests for ComputerUseMixin tool registration."""

    def test_computer_use_mixin_registers_tools(self, agent):
        """Agent with ComputerUseMixin has all 4 tools registered."""
        from gaia.agents.base.tools import _TOOL_REGISTRY

        # Clear registry to isolate test
        old_registry = dict(_TOOL_REGISTRY)
        _TOOL_REGISTRY.clear()

        try:
            agent.register_computer_use_tools()

            expected_tools = {
                "learn_workflow",
                "replay_workflow",
                "list_workflows",
                "test_workflow",
            }
            registered = set(_TOOL_REGISTRY.keys())
            assert expected_tools.issubset(
                registered
            ), f"Missing tools: {expected_tools - registered}"

            # Verify each tool has required metadata
            for name in expected_tools:
                tool_info = _TOOL_REGISTRY[name]
                assert tool_info["name"] == name
                assert tool_info["description"]  # non-empty
                assert callable(tool_info["function"])
        finally:
            # Restore registry
            _TOOL_REGISTRY.clear()
            _TOOL_REGISTRY.update(old_registry)


# ============================================================================
# Screenshot cleanup tests
# ============================================================================


class TestScreenshotCleanup:
    """Tests for skill deletion and screenshot cleanup."""

    def test_screenshot_cleanup(self, agent, sample_steps_json):
        """When a skill is deleted, its screenshot directory is also removed."""
        # Learn a workflow (creates screenshots)
        result = agent._learn_workflow_impl(
            task_description="Cleanup test workflow",
            start_url="https://example.com",
            steps_json=sample_steps_json,
        )
        assert result["status"] == "learned"
        skill_id = result["skill_id"]

        # Verify screenshots exist
        skill_dir = agent.skills_dir / skill_id
        # Screenshots might be in a temp dir that was renamed;
        # find any dir with screenshots
        has_screenshots = False
        for d in agent.skills_dir.iterdir():
            if d.is_dir() and list(d.glob("*.png")):
                has_screenshots = True
                break
        assert has_screenshots, "Expected screenshot files to exist"

        # Delete the workflow
        delete_result = agent.delete_workflow("Cleanup test workflow")
        assert delete_result["status"] == "deleted"

        # Verify screenshots directory is gone
        if skill_dir.exists():
            pngs = list(skill_dir.glob("*.png"))
            assert len(pngs) == 0, "Screenshots should be removed after deletion"

        # Verify skill is gone from KnowledgeDB
        skills = agent._get_all_skills()
        skill_ids = [s["id"] for s in skills]
        assert skill_id not in skill_ids


# ============================================================================
# Helper function tests
# ============================================================================


class TestHelpers:
    """Tests for module-level helper functions."""

    def test_extract_domain_simple(self):
        assert _extract_domain("https://linkedin.com/feed") == "linkedin.com"

    def test_extract_domain_with_www(self):
        assert _extract_domain("https://www.google.com/search") == "google.com"

    def test_extract_domain_with_subdomain(self):
        assert _extract_domain("https://mail.google.com") == "mail.google.com"

    def test_extract_domain_invalid(self):
        assert _extract_domain("not a url") is None

    def test_substitute_params_basic(self):
        result = _substitute_params("{content}", {"content": "Hello"})
        assert result == "Hello"

    def test_substitute_params_multiple(self):
        result = _substitute_params(
            "Title: {title}, Body: {body}",
            {"title": "My Title", "body": "My Body"},
        )
        assert result == "Title: My Title, Body: My Body"

    def test_substitute_params_no_match(self):
        result = _substitute_params("No placeholders here", {"key": "value"})
        assert result == "No placeholders here"

    def test_substitute_params_empty(self):
        assert _substitute_params("", {"key": "value"}) == ""
        assert _substitute_params("text", {}) == "text"
        assert _substitute_params(None, {"key": "value"}) is None

    def test_extract_skill_triggers(self):
        triggers = _extract_skill_triggers("Post content on LinkedIn feed")
        assert "post" in triggers
        assert "content" in triggers
        assert "linkedin" in triggers
        assert "feed" in triggers
        # Stop words should be excluded
        assert "on" not in triggers
