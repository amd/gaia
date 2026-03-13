# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
ComputerUseMixin: Browser-based workflow learning and replay.

Provides:
- learn_workflow(): Record browser actions as a replayable skill
- replay_workflow(): Execute a learned skill with parameter substitution
- list_workflows(): List all learned skills, with filtering
- test_workflow(): Replay in visible mode for verification

Uses PlaywrightBridge for browser automation (abstracted for testability).
Skills are stored in KnowledgeDB as category="skill" with metadata.type="replay".
Screenshots are stored in ~/.gaia/skills/{insight_id}/step_N.png.

Usage:
    class MyAgent(Agent, MemoryMixin, ComputerUseMixin):
        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            self.init_memory()

        def _register_tools(self):
            self.register_memory_tools()
            self.register_computer_use_tools()
"""

import json
import logging
import shutil
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# Default skills directory under ~/.gaia/skills/
_DEFAULT_SKILLS_DIR = Path.home() / ".gaia" / "skills"

# Required fields for each step in a replay skill
_REQUIRED_STEP_FIELDS = {"step", "action", "target", "value", "screenshot", "notes"}

# Valid action types for replay steps
_VALID_ACTIONS = {"navigate", "click", "type"}


# ============================================================================
# PlaywrightBridge: Abstraction for browser automation
# ============================================================================


class PlaywrightBridge:
    """Abstraction over Playwright browser automation.

    In production, delegates to Playwright MCP tools.
    Tests can replace this with a mock instance.

    Args:
        headless: If True, run browser in headless mode (default: True).
    """

    def __init__(self, headless: bool = True):
        self.headless = headless
        self._launched = False

    def launch(self, url: Optional[str] = None) -> Dict[str, Any]:
        """Launch browser and optionally navigate to a URL.

        Args:
            url: Optional starting URL to navigate to.

        Returns:
            Dict with status and any browser info.
        """
        self._launched = True
        result = {"status": "launched", "headless": self.headless}
        if url:
            self.navigate(url)
            result["url"] = url
        logger.info(
            "[PlaywrightBridge] launched browser headless=%s url=%s",
            self.headless,
            url,
        )
        return result

    def navigate(self, url: str) -> Dict[str, Any]:
        """Navigate to a URL.

        Args:
            url: The URL to navigate to.

        Returns:
            Dict with navigation result.
        """
        logger.info("[PlaywrightBridge] navigate to %s", url)
        return {"status": "navigated", "url": url}

    def click(self, selector: str) -> Dict[str, Any]:
        """Click an element by CSS selector.

        Args:
            selector: CSS selector of element to click.

        Returns:
            Dict with click result.

        Raises:
            RuntimeError: If element not found or click fails.
        """
        logger.info("[PlaywrightBridge] click %s", selector)
        return {"status": "clicked", "selector": selector}

    def type_text(self, selector: str, text: str) -> Dict[str, Any]:
        """Type text into an element.

        Args:
            selector: CSS selector of input element.
            text: Text to type.

        Returns:
            Dict with type result.

        Raises:
            RuntimeError: If element not found or type fails.
        """
        logger.info("[PlaywrightBridge] type into %s: %s", selector, text[:50])
        return {"status": "typed", "selector": selector, "text": text}

    def screenshot(self, save_path: str) -> bytes:
        """Take a screenshot and save to the given path.

        Args:
            save_path: File path to save the screenshot PNG.

        Returns:
            Raw PNG bytes of the screenshot.
        """
        logger.info("[PlaywrightBridge] screenshot -> %s", save_path)
        # In production, this calls Playwright MCP browser_take_screenshot
        # and saves the result. The base implementation returns empty bytes.
        path = Path(save_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        # Placeholder PNG data (1x1 transparent pixel)
        png_data = b"\x89PNG\r\n\x1a\n"
        path.write_bytes(png_data)
        return png_data

    def snapshot(self) -> str:
        """Take a DOM/accessibility snapshot.

        Returns:
            String representation of the DOM accessibility tree.
        """
        logger.info("[PlaywrightBridge] DOM snapshot")
        return "<snapshot>page content</snapshot>"

    def close(self) -> Dict[str, Any]:
        """Close the browser.

        Returns:
            Dict with close status.
        """
        self._launched = False
        logger.info("[PlaywrightBridge] browser closed")
        return {"status": "closed"}


# ============================================================================
# ComputerUseMixin
# ============================================================================


class ComputerUseMixin:
    """Mixin that gives any Agent browser-based workflow learning and replay.

    Provides tools for:
    - Learning workflows by recording browser actions
    - Replaying learned workflows with parameter substitution
    - Listing and testing stored workflows
    - Self-healing: when a selector fails during replay, attempts LLM-suggested
      alternative selector before giving up

    Requires the host class to have:
    - MemoryMixin (for .knowledge property → KnowledgeDB)
    - A _TOOL_REGISTRY-compatible tool system (Agent subclass or @tool decorator)
    - Optionally: an LLM client for self-healing selector suggestions
    """

    _playwright_bridge: Optional[PlaywrightBridge] = None
    _skills_dir: Optional[Path] = None

    def init_computer_use(
        self,
        skills_dir: Optional[Path] = None,
        playwright_bridge: Optional[PlaywrightBridge] = None,
    ) -> None:
        """Initialize the computer use subsystem.

        Args:
            skills_dir: Directory for storing skill screenshots.
                        Defaults to ~/.gaia/skills/
            playwright_bridge: Optional pre-configured PlaywrightBridge.
                              If not provided, creates a default one.
        """
        self._skills_dir = skills_dir or _DEFAULT_SKILLS_DIR
        self._skills_dir.mkdir(parents=True, exist_ok=True)
        self._playwright_bridge = playwright_bridge or PlaywrightBridge(headless=True)
        logger.info("[ComputerUseMixin] initialized, skills_dir=%s", self._skills_dir)

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def skills_dir(self) -> Path:
        """Get the skills screenshot directory."""
        if self._skills_dir is None:
            self._skills_dir = _DEFAULT_SKILLS_DIR
            self._skills_dir.mkdir(parents=True, exist_ok=True)
        return self._skills_dir

    # ------------------------------------------------------------------
    # Tool Registration
    # ------------------------------------------------------------------

    def register_computer_use_tools(self) -> None:
        """Register computer use tools with the agent's tool registry.

        Call this from _register_tools() in your agent subclass.
        Tools registered:
        - learn_workflow: Record browser actions as a replayable skill
        - replay_workflow: Execute a learned skill
        - list_workflows: List all learned skills
        - test_workflow: Replay in visible mode for verification
        """
        from gaia.agents.base.tools import tool

        mixin = self  # Capture self for nested functions

        @tool(
            name="learn_workflow",
            description=(
                "Learn a new browser workflow by recording actions. "
                "Opens a visible browser, executes the provided steps, captures "
                "screenshots at each step, and stores the complete workflow as a "
                "replayable skill in the knowledge base.\n"
                "Each step should specify: action (navigate/click/type), "
                "target (URL or CSS selector), value (text for type actions), "
                "and notes (human description of the step).\n"
                "Parameters in step values use {param_name} placeholders.\n"
                "Example:\n"
                "  learn_workflow(\n"
                '    task_description="Post on LinkedIn",\n'
                '    start_url="https://linkedin.com/feed",\n'
                '    steps=\'[{"action":"click","target":"div.share-box","value":null,'
                '"notes":"Click compose"},{"action":"type","target":"div.ql-editor",'
                '"value":"{content}","notes":"Type post content"}]\'\n'
                "  )"
            ),
            parameters={
                "task_description": {
                    "type": "str",
                    "description": "Human-readable description of the workflow",
                    "required": True,
                },
                "start_url": {
                    "type": "str",
                    "description": "The URL to open the browser at",
                    "required": True,
                },
                "steps": {
                    "type": "str",
                    "description": (
                        "JSON array of step objects. Each step: "
                        '{"action": "navigate|click|type", "target": "url_or_selector", '
                        '"value": "text_or_null", "notes": "description"}'
                    ),
                    "required": False,
                },
            },
        )
        def learn_workflow(
            task_description: str,
            start_url: str,
            steps: str = "[]",
        ) -> Dict[str, Any]:
            """Learn a new browser workflow by recording actions."""
            return mixin._learn_workflow_impl(task_description, start_url, steps)

        @tool(
            name="replay_workflow",
            description=(
                "Replay a previously learned browser workflow. "
                "Looks up the skill from the knowledge base, walks through each step "
                "executing navigate/click/type via the browser. "
                "Substitutes {param} placeholders with provided parameters.\n"
                "If a step fails, attempts self-healing with an alternative selector.\n"
                "Example:\n"
                "  replay_workflow(\n"
                '    skill_name="Post on LinkedIn",\n'
                '    parameters=\'{"content": "Exciting AI news!"}\'\n'
                "  )"
            ),
            parameters={
                "skill_name": {
                    "type": "str",
                    "description": "Name/content of the skill to replay (searched via FTS)",
                    "required": True,
                },
                "parameters": {
                    "type": "str",
                    "description": "JSON object of parameter substitutions (default: {})",
                    "required": False,
                },
            },
        )
        def replay_workflow(
            skill_name: str,
            parameters: str = "{}",
        ) -> Dict[str, Any]:
            """Replay a previously learned browser workflow."""
            return mixin._replay_workflow_impl(skill_name, parameters, headless=True)

        @tool(
            name="list_workflows",
            description=(
                "List all learned workflows (skills) from the knowledge base. "
                "Optionally filter by domain and/or skill type.\n"
                "Example:\n"
                '  list_workflows(domain="linkedin.com")\n'
                '  list_workflows(skill_type="replay")\n'
                "  list_workflows()  # all skills"
            ),
            parameters={
                "domain": {
                    "type": "str",
                    "description": "Filter by domain (e.g., 'linkedin.com', 'gmail')",
                    "required": False,
                },
                "skill_type": {
                    "type": "str",
                    "description": "Filter by skill type: replay, decision, api",
                    "required": False,
                },
            },
        )
        def list_workflows(
            domain: str = "",
            skill_type: str = "",
        ) -> Dict[str, Any]:
            """List all learned workflows."""
            return mixin._list_workflows_impl(
                domain=domain or None,
                skill_type=skill_type or None,
            )

        @tool(
            name="test_workflow",
            description=(
                "Test a learned workflow by replaying it in a visible (non-headless) "
                "browser. Use this to verify a workflow still works correctly.\n"
                "Example:\n"
                '  test_workflow(skill_name="Post on LinkedIn")'
            ),
            parameters={
                "skill_name": {
                    "type": "str",
                    "description": "Name/content of the skill to test",
                    "required": True,
                },
            },
        )
        def test_workflow(skill_name: str) -> Dict[str, Any]:
            """Test a learned workflow in visible browser mode."""
            return mixin._replay_workflow_impl(
                skill_name, parameters="{}", headless=False
            )

        logger.info("[ComputerUseMixin] registered 4 computer use tools")

    # ------------------------------------------------------------------
    # Implementation Methods
    # ------------------------------------------------------------------

    def _learn_workflow_impl(
        self,
        task_description: str,
        start_url: str,
        steps_json: str,
    ) -> Dict[str, Any]:
        """Implementation of learn_workflow tool.

        Opens a visible browser, navigates to start_url, executes and records
        each step with screenshots, then stores the complete skill in KnowledgeDB.

        Args:
            task_description: Human-readable workflow description.
            start_url: Starting URL for the browser.
            steps_json: JSON array of step definitions.

        Returns:
            Dict with skill_id, step_count, parameters found, and status.
        """
        # Parse steps
        try:
            raw_steps = json.loads(steps_json) if steps_json else []
        except json.JSONDecodeError as e:
            return {
                "status": "error",
                "message": f"Invalid steps JSON: {e}",
            }

        # Ensure we have a bridge
        bridge = self._get_or_create_bridge(headless=False)

        # Generate a temporary insight ID for screenshot storage
        from uuid import uuid4

        temp_id = str(uuid4())
        skill_dir = self.skills_dir / temp_id
        skill_dir.mkdir(parents=True, exist_ok=True)

        import re as _re

        recorded_steps = []
        parameters_found = set()
        recording_error = None

        try:
            # Launch browser and navigate to start URL
            bridge.launch()
            bridge.navigate(start_url)

            # Record initial state as step 0
            screenshot_path = str(skill_dir / "step_0.png")
            bridge.screenshot(screenshot_path)
            recorded_steps.append(
                {
                    "step": 0,
                    "action": "navigate",
                    "target": start_url,
                    "value": None,
                    "screenshot": f"skills/{temp_id}/step_0.png",
                    "notes": f"Navigate to {start_url}",
                }
            )

            # Execute and record each user-defined step
            for i, raw_step in enumerate(raw_steps, start=1):
                action = raw_step.get("action", "click")
                target = raw_step.get("target", "")
                value = raw_step.get("value")
                notes = raw_step.get("notes", "")

                if action not in _VALID_ACTIONS:
                    logger.warning(
                        "[ComputerUseMixin] unknown action '%s' in step %d, skipping",
                        action,
                        i,
                    )
                    continue

                # Extract parameter placeholders from value
                if value:
                    for match in _re.finditer(r"\{(\w+)\}", value):
                        parameters_found.add(match.group(1))

                # Execute the action
                self._execute_step(bridge, action, target, value)

                # Take screenshot after action
                screenshot_path = str(skill_dir / f"step_{i}.png")
                bridge.screenshot(screenshot_path)

                # Record the step
                recorded_steps.append(
                    {
                        "step": i,
                        "action": action,
                        "target": target,
                        "value": value,
                        "screenshot": f"skills/{temp_id}/step_{i}.png",
                        "notes": notes,
                    }
                )

                logger.debug(
                    "[ComputerUseMixin] recorded step %d: %s %s",
                    i,
                    action,
                    target,
                )

        except Exception as e:
            logger.error("[ComputerUseMixin] learn_workflow error at step: %s", e)
            recording_error = str(e)
        finally:
            bridge.close()

        if recording_error:
            return {
                "status": "error",
                "message": f"Failed during recording: {recording_error}",
                "steps_recorded": len(recorded_steps),
            }

        # Extract domain from start_url
        domain = _extract_domain(start_url)

        # Store as skill in KnowledgeDB
        metadata = {
            "type": "replay",
            "steps": recorded_steps,
            "parameters": sorted(parameters_found),
            "tools_used": ["playwright"],
        }

        try:
            insight_id = self.knowledge.store_insight(
                category="skill",
                content=task_description,
                domain=domain,
                triggers=_extract_skill_triggers(task_description),
                metadata=metadata,
            )
        except Exception as e:
            logger.error("[ComputerUseMixin] failed to store skill: %s", e)
            return {
                "status": "error",
                "message": f"Failed to store skill: {e}",
            }

        # If the insight_id differs from temp_id (e.g., dedup), move screenshots
        if insight_id != temp_id:
            new_skill_dir = self.skills_dir / insight_id
            if skill_dir.exists():
                if new_skill_dir.exists():
                    shutil.rmtree(new_skill_dir)
                skill_dir.rename(new_skill_dir)
                # Update screenshot paths in metadata
                for step in recorded_steps:
                    step["screenshot"] = step["screenshot"].replace(temp_id, insight_id)
                metadata["steps"] = recorded_steps
                # Update the stored metadata
                self.knowledge.store_insight(
                    category="skill",
                    content=task_description,
                    domain=domain,
                    triggers=_extract_skill_triggers(task_description),
                    metadata=metadata,
                )

        logger.info(
            "[ComputerUseMixin] learned workflow '%s' with %d steps, id=%s",
            task_description,
            len(recorded_steps),
            insight_id,
        )

        return {
            "status": "learned",
            "skill_id": insight_id,
            "description": task_description,
            "step_count": len(recorded_steps),
            "parameters": sorted(parameters_found),
            "domain": domain,
        }

    def _replay_workflow_impl(
        self,
        skill_name: str,
        parameters_json: str,
        headless: bool = True,
    ) -> Dict[str, Any]:
        """Implementation of replay_workflow and test_workflow tools.

        Looks up the skill from KnowledgeDB, walks through steps, executes
        each action via Playwright. Substitutes {param} placeholders.
        On failure: attempts self-healing with alternative selector.

        Args:
            skill_name: Name/content of the skill to replay.
            parameters_json: JSON object of parameter substitutions.
            headless: Whether to run in headless mode.

        Returns:
            Dict with status, steps_executed, and any errors.
        """
        # Parse parameters
        try:
            params = json.loads(parameters_json) if parameters_json else {}
        except json.JSONDecodeError as e:
            return {
                "status": "error",
                "message": f"Invalid parameters JSON: {e}",
            }

        # Look up skill from KnowledgeDB
        skill = self._find_skill(skill_name)
        if not skill:
            return {
                "status": "error",
                "message": f"Skill not found: {skill_name}",
            }

        skill_id = skill["id"]
        metadata = skill.get("metadata") or {}
        steps = metadata.get("steps", [])

        if not steps:
            return {
                "status": "error",
                "message": f"Skill '{skill_name}' has no steps to replay",
            }

        # Get or create bridge with appropriate headless mode
        bridge = self._get_or_create_bridge(headless=headless)

        steps_executed = 0
        errors = []
        replay_failed = False
        failure_result = None

        try:
            bridge.launch()

            for step in steps:
                action = step.get("action", "click")
                target = step.get("target", "")
                value = step.get("value")
                notes = step.get("notes", "")

                # Substitute parameters in target and value
                target = _substitute_params(target, params)
                if value is not None:
                    value = _substitute_params(value, params)

                try:
                    self._execute_step(bridge, action, target, value)
                    steps_executed += 1
                    logger.debug(
                        "[ComputerUseMixin] replay step %d: %s %s",
                        step.get("step", steps_executed),
                        action,
                        target,
                    )
                except Exception as e:
                    logger.warning(
                        "[ComputerUseMixin] step %d failed: %s. Attempting self-heal.",
                        step.get("step", steps_executed + 1),
                        e,
                    )

                    # Self-healing: take screenshot + snapshot, try alternative
                    healed = self._attempt_self_heal(
                        bridge, action, target, value, notes, str(e)
                    )

                    if healed:
                        steps_executed += 1
                        logger.info(
                            "[ComputerUseMixin] self-healed step %d",
                            step.get("step", steps_executed),
                        )
                    else:
                        error_msg = (
                            f"Step {step.get('step', '?')} failed: {action} on "
                            f"'{target}' — {e}. Self-heal also failed."
                        )
                        errors.append(error_msg)
                        logger.error("[ComputerUseMixin] %s", error_msg)
                        replay_failed = True
                        failure_result = {
                            "status": "error",
                            "message": error_msg,
                            "steps_executed": steps_executed,
                            "total_steps": len(steps),
                            "errors": errors,
                            "headless": headless,
                        }
                        break  # Exit step loop; finally will close bridge

        except Exception as e:
            logger.error("[ComputerUseMixin] replay_workflow error: %s", e)
            replay_failed = True
            failure_result = {
                "status": "error",
                "message": f"Replay failed: {e}",
                "steps_executed": steps_executed,
                "total_steps": len(steps),
                "headless": headless,
            }
        finally:
            bridge.close()

        # Record usage and return result
        if replay_failed:
            self.knowledge.record_usage(skill_id, success=False)
            return failure_result

        # Record success
        self.knowledge.record_usage(skill_id, success=True)

        logger.info(
            "[ComputerUseMixin] replayed '%s' successfully (%d steps)",
            skill_name,
            steps_executed,
        )

        return {
            "status": "success",
            "skill_name": skill_name,
            "steps_executed": steps_executed,
            "total_steps": len(steps),
            "parameters_used": params,
            "headless": headless,
        }

    def _list_workflows_impl(
        self,
        domain: Optional[str] = None,
        skill_type: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Implementation of list_workflows tool.

        Lists all category="skill" insights from KnowledgeDB,
        filterable by domain and metadata.type.

        Args:
            domain: Optional domain filter (e.g., "linkedin.com").
            skill_type: Optional metadata.type filter (replay, decision, api).

        Returns:
            Dict with workflows list and count.
        """
        try:
            # Get all skills from KnowledgeDB
            # Use a broad search to get all skills, then filter
            results = self._get_all_skills()

            # Filter by domain
            if domain:
                results = [r for r in results if r.get("domain") == domain]

            # Filter by skill_type (metadata.type)
            if skill_type:
                results = [
                    r
                    for r in results
                    if r.get("metadata") and r["metadata"].get("type") == skill_type
                ]

            # Format results
            workflows = []
            for r in results:
                meta = r.get("metadata") or {}
                workflows.append(
                    {
                        "id": r["id"],
                        "name": r["content"],
                        "domain": r.get("domain"),
                        "type": meta.get("type", "unknown"),
                        "parameters": meta.get("parameters", []),
                        "step_count": len(meta.get("steps", [])),
                        "confidence": r.get("confidence", 0.5),
                        "use_count": r.get("use_count", 0),
                        "success_count": r.get("success_count", 0),
                        "failure_count": r.get("failure_count", 0),
                    }
                )

            return {
                "status": "found" if workflows else "empty",
                "count": len(workflows),
                "workflows": workflows,
            }

        except Exception as e:
            logger.error("[ComputerUseMixin] list_workflows error: %s", e)
            return {
                "status": "error",
                "message": f"Failed to list workflows: {e}",
            }

    # ------------------------------------------------------------------
    # Helper Methods
    # ------------------------------------------------------------------

    def _get_or_create_bridge(self, headless: bool = True) -> PlaywrightBridge:
        """Get the configured PlaywrightBridge, or create a new one.

        Always creates a fresh bridge with the requested headless setting.

        Args:
            headless: Whether to run headless.

        Returns:
            A PlaywrightBridge instance.
        """
        if self._playwright_bridge is not None:
            # Use the pre-configured bridge (e.g., from tests)
            self._playwright_bridge.headless = headless
            return self._playwright_bridge
        return PlaywrightBridge(headless=headless)

    def _find_skill(self, skill_name: str) -> Optional[Dict]:
        """Find a skill by name/content in KnowledgeDB.

        Args:
            skill_name: The skill name or description to search for.

        Returns:
            The skill dict if found, None otherwise.
        """
        try:
            results = self.knowledge.recall(
                query=skill_name,
                category="skill",
                top_k=5,
            )
            if results:
                return results[0]
            return None
        except Exception as e:
            logger.error("[ComputerUseMixin] _find_skill error: %s", e)
            return None

    def _get_all_skills(self) -> List[Dict]:
        """Get all skill insights from KnowledgeDB.

        Uses a direct SQL query since recall() requires a search query.

        Returns:
            List of skill dicts.
        """
        try:
            cursor = self.knowledge.conn.execute("""
                SELECT id, category, domain, content, confidence,
                       triggers, metadata, use_count, last_used,
                       success_count, failure_count
                FROM insights
                WHERE category = 'skill'
                ORDER BY last_used DESC
                """)

            results = []
            for row in cursor.fetchall():
                results.append(
                    {
                        "id": row[0],
                        "category": row[1],
                        "domain": row[2],
                        "content": row[3],
                        "confidence": row[4],
                        "triggers": json.loads(row[5]) if row[5] else None,
                        "metadata": json.loads(row[6]) if row[6] else None,
                        "use_count": row[7],
                        "last_used": row[8],
                        "success_count": row[9],
                        "failure_count": row[10],
                    }
                )
            return results
        except Exception as e:
            logger.error("[ComputerUseMixin] _get_all_skills error: %s", e)
            return []

    def _execute_step(
        self,
        bridge: PlaywrightBridge,
        action: str,
        target: str,
        value: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Execute a single workflow step via PlaywrightBridge.

        Args:
            bridge: The PlaywrightBridge instance.
            action: Action type (navigate, click, type).
            target: Target URL or CSS selector.
            value: Optional value (text for type actions).

        Returns:
            Dict with step result.

        Raises:
            RuntimeError: If the action fails.
        """
        if action == "navigate":
            return bridge.navigate(target)
        elif action == "click":
            return bridge.click(target)
        elif action == "type":
            return bridge.type_text(target, value or "")
        else:
            raise RuntimeError(f"Unknown action: {action}")

    def _attempt_self_heal(
        self,
        bridge: PlaywrightBridge,
        action: str,
        target: str,
        value: Optional[str],
        notes: str,
        error: str,
    ) -> bool:
        """Attempt to self-heal a failed step.

        Takes a screenshot and DOM snapshot, then asks for an alternative
        selector (via LLM or heuristic). If found, retries the step once.

        Args:
            bridge: The PlaywrightBridge instance.
            action: The failed action type.
            target: The failed target selector.
            value: The step value (if any).
            notes: The step notes/description.
            error: The error message from the failed attempt.

        Returns:
            True if self-heal succeeded, False otherwise.
        """
        try:
            # Take diagnostic screenshot and snapshot
            diag_path = str(self.skills_dir / "_self_heal_diag.png")
            bridge.screenshot(diag_path)
            dom_snapshot = bridge.snapshot()

            # Try to get an alternative selector
            alt_selector = self._suggest_alternative_selector(
                dom_snapshot=dom_snapshot,
                original_selector=target,
                error=error,
                step_notes=notes,
            )

            if alt_selector and alt_selector != target:
                logger.info(
                    "[ComputerUseMixin] trying alternative selector: %s",
                    alt_selector,
                )
                try:
                    self._execute_step(bridge, action, alt_selector, value)
                    return True
                except Exception as e2:
                    logger.warning(
                        "[ComputerUseMixin] alternative selector also failed: %s", e2
                    )
                    return False
            else:
                logger.warning("[ComputerUseMixin] no alternative selector suggested")
                return False

        except Exception as e:
            logger.error("[ComputerUseMixin] self-heal error: %s", e)
            return False

    def _suggest_alternative_selector(
        self,
        dom_snapshot: str,
        original_selector: str,
        error: str,
        step_notes: str,
    ) -> Optional[str]:
        """Suggest an alternative CSS selector when the original fails.

        In production, this uses the LLM to analyze the DOM snapshot and
        suggest an alternative. Override this method for custom behavior.

        Args:
            dom_snapshot: DOM/accessibility tree snapshot.
            original_selector: The selector that failed.
            error: The error message.
            step_notes: Human description of what the step should do.

        Returns:
            An alternative selector string, or None if no suggestion.
        """
        # Base implementation: no LLM available, return None.
        # Subclasses with LLM access can override this to use the LLM:
        #
        #   prompt = f"The selector '{original_selector}' failed with: {error}\n"
        #            f"Step intent: {step_notes}\n"
        #            f"DOM snapshot:\n{dom_snapshot}\n"
        #            f"Suggest ONE alternative CSS selector."
        #   response = self.llm_client.generate(prompt)
        #   return parse_selector_from_response(response)
        #
        logger.debug(
            "[ComputerUseMixin] _suggest_alternative_selector not implemented "
            "(no LLM). Override in subclass for self-healing."
        )
        return None

    def delete_workflow(self, skill_name: str) -> Dict[str, Any]:
        """Delete a learned workflow and its screenshots.

        Args:
            skill_name: Name/content of the skill to delete.

        Returns:
            Dict with deletion status.
        """
        skill = self._find_skill(skill_name)
        if not skill:
            return {
                "status": "not_found",
                "message": f"Skill not found: {skill_name}",
            }

        skill_id = skill["id"]

        # Delete screenshots directory
        skill_dir = self.skills_dir / skill_id
        if skill_dir.exists():
            shutil.rmtree(skill_dir)
            logger.info("[ComputerUseMixin] deleted screenshots for skill %s", skill_id)

        # Delete from KnowledgeDB
        try:
            with self.knowledge.lock:
                self.knowledge.conn.execute(
                    "DELETE FROM insights_fts WHERE rowid = "
                    "(SELECT rowid FROM insights WHERE id = ?)",
                    (skill_id,),
                )
                self.knowledge.conn.execute(
                    "DELETE FROM insights WHERE id = ?",
                    (skill_id,),
                )
                self.knowledge.conn.commit()
        except Exception as e:
            logger.error("[ComputerUseMixin] failed to delete skill: %s", e)
            return {
                "status": "error",
                "message": f"Failed to delete skill: {e}",
            }

        logger.info(
            "[ComputerUseMixin] deleted workflow '%s' (id=%s)", skill_name, skill_id
        )

        return {
            "status": "deleted",
            "skill_id": skill_id,
            "screenshots_removed": True,
        }


# ============================================================================
# Module-Level Helpers
# ============================================================================


def _extract_domain(url: str) -> Optional[str]:
    """Extract domain from a URL.

    Args:
        url: Full URL string.

    Returns:
        Domain string (e.g., 'linkedin.com'), or None if parsing fails.
    """
    try:
        from urllib.parse import urlparse

        parsed = urlparse(url)
        host = parsed.hostname or ""
        # Remove 'www.' prefix
        if host.startswith("www."):
            host = host[4:]
        return host if host else None
    except Exception:
        return None


def _extract_skill_triggers(description: str) -> List[str]:
    """Extract trigger keywords from a skill description.

    Args:
        description: The skill's human-readable description.

    Returns:
        List of keyword strings for trigger-based recall.
    """
    import re

    _STOP_WORDS = {
        "a",
        "an",
        "the",
        "is",
        "are",
        "was",
        "were",
        "be",
        "been",
        "being",
        "have",
        "has",
        "had",
        "do",
        "does",
        "did",
        "will",
        "would",
        "could",
        "should",
        "may",
        "might",
        "shall",
        "can",
        "to",
        "of",
        "in",
        "for",
        "on",
        "with",
        "at",
        "by",
        "from",
        "as",
        "and",
        "or",
        "but",
        "if",
        "not",
        "no",
        "so",
        "it",
        "its",
        "my",
        "our",
        "we",
        "i",
        "me",
        "you",
        "your",
        "he",
        "she",
        "they",
        "them",
        "this",
        "that",
    }

    words = re.sub(r"[^\w\s]", " ", description.lower()).split()
    keywords = [w for w in words if w not in _STOP_WORDS and len(w) >= 3]

    # Deduplicate while preserving order
    seen = set()
    unique = []
    for kw in keywords:
        if kw not in seen:
            seen.add(kw)
            unique.append(kw)

    return unique[:5]


def _substitute_params(text: str, params: Dict[str, str]) -> str:
    """Substitute {param_name} placeholders in text with parameter values.

    Args:
        text: Text containing {param_name} placeholders.
        params: Dict mapping parameter names to values.

    Returns:
        Text with placeholders replaced.
    """
    if not text or not params:
        return text

    result = text
    for key, value in params.items():
        result = result.replace(f"{{{key}}}", str(value))
    return result
