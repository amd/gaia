#!/usr/bin/env python
# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Confirmation-guardrail tests for CodeAgent's orchestrator tool executor.

The executor returned by ``CodeAgent._create_tool_executor`` used to call the
tool registry directly, bypassing ``Agent._execute_tool`` and therefore the
user-confirmation gate entirely. These tests pin the gate in place:

- a denied gated tool must not run its body (asserted on a filesystem canary)
- an MCP-registered tool reached through the same executor is gated too
- a denial surfaces as a clear checklist failure — never a silent success,
  never a retry loop that re-prompts the user
- non-gated tools are unaffected
"""

import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from gaia_agent_code.agent import CodeAgent
from gaia_agent_code.orchestration.checklist_executor import (
    TEMPLATE_TO_TOOL,
    ChecklistExecutor,
)
from gaia_agent_code.orchestration.checklist_generator import (
    ChecklistItem,
    GeneratedChecklist,
)
from gaia_agent_code.orchestration.steps.base import UserContext
from gaia_agent_code.orchestration.steps.error_handler import (
    ErrorHandler,
    RecoveryAction,
)

from gaia.agents.base.agent import tool_execution_timeout
from gaia.agents.base.console import SilentConsole
from gaia.agents.base.tools import _TOOL_REGISTRY, tool


class RecordingConsole(SilentConsole):
    """Silent console that records confirmation prompts and answers `allow`."""

    def __init__(self, allow: bool):
        super().__init__()
        self.allow = allow
        self.prompts = []
        self.errors = []

    def confirm_tool_execution(self, tool_name, tool_args):
        self.prompts.append((tool_name, dict(tool_args)))
        return self.allow

    def print_error(self, *args, **kwargs):
        self.errors.append(args[0] if args else "")


def make_context(project_dir: str) -> UserContext:
    return UserContext(
        user_request="Test",
        project_dir=project_dir,
        language="typescript",
        project_type="fullstack",
    )


class TestExecutorEnforcesConfirmation(unittest.TestCase):
    """The orchestrator executor must route through the confirmation gate."""

    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.agent = CodeAgent(silent_mode=True, max_steps=5)
        self.agent._register_tools()
        self.agent.path_validator.add_allowed_path(self.test_dir)
        self.console = RecordingConsole(allow=False)
        self.agent.console = self.console
        self.execute_tool = self.agent._create_tool_executor()

    def tearDown(self):
        if os.path.exists(self.test_dir):
            shutil.rmtree(self.test_dir, ignore_errors=True)
        _TOOL_REGISTRY.clear()

    def test_denied_write_file_never_touches_disk(self):
        """A denied write_file must return a denial AND create no file."""
        canary = Path(self.test_dir) / "canary.txt"
        self.assertFalse(canary.exists())

        result = self.execute_tool(
            "write_file", {"file_path": str(canary), "content": "pwned"}
        )

        self.assertEqual(result.get("status"), "denied")
        self.assertIn("denied", result.get("error", "").lower())
        # The side-effect canary is the real assertion: the body never ran.
        self.assertFalse(canary.exists(), "write_file executed despite being denied")
        self.assertEqual([p[0] for p in self.console.prompts], ["write_file"])

    def test_denied_run_cli_command_never_executes(self):
        """A denied shell command must not spawn a subprocess."""
        canary = Path(self.test_dir) / "shell_canary.txt"
        self.assertFalse(canary.exists())

        args = {
            "command": f'echo pwned > "{canary}"',
            "working_dir": self.test_dir,
        }

        result = self.execute_tool("run_cli_command", args)

        self.assertEqual(result.get("status"), "denied")
        self.assertFalse(
            canary.exists(), "run_cli_command executed despite being denied"
        )
        self.assertEqual([p[0] for p in self.console.prompts], ["run_cli_command"])

        # Positive control: the very same command DOES create the canary when
        # approved, so the assertion above cannot pass just because the shell
        # redirect was a no-op on this platform.
        self.agent.console = RecordingConsole(allow=True)
        self.agent._create_tool_executor()("run_cli_command", args)
        self.assertTrue(canary.exists(), "positive control failed to run")

    def test_approved_gated_tool_still_runs(self):
        """Approving at the prompt must let the tool through unchanged."""
        self.agent.console = RecordingConsole(allow=True)
        execute_tool = self.agent._create_tool_executor()
        target = Path(self.test_dir) / "allowed.txt"

        execute_tool("write_file", {"file_path": str(target), "content": "ok"})

        self.assertTrue(target.exists())
        self.assertEqual(target.read_text(encoding="utf-8"), "ok")

    def test_mcp_style_tool_is_gated_when_declared(self):
        """A co-resident MCP tool is gated once it's in the confirmation set.

        CodeAgent never calls ``_snapshot_tools()``, so its registry IS the
        process-global one — an MCP tool registered by another agent in the same
        Agent UI process is reachable through this executor. This pins the
        executor half of that: whatever ``confirmation_required_tools()``
        contains is enforced here.

        NOTE: nothing currently *adds* MCP tool names to that set — the base
        ``CONFIRMATION_REQUIRED_TOOLS`` is empty and only EmailTriageAgent
        populates it. Classifying write-capable MCP tools is the companion
        change and is NOT in this branch; this test declares the tool
        explicitly to stand in for it.
        """
        canary = Path(self.test_dir) / "mcp_canary.txt"
        ran = []

        @tool
        def mcp_files_write_file(path: str) -> dict:
            """MCP-style write tool registered by a co-resident agent."""
            ran.append(path)
            Path(path).write_text("pwned", encoding="utf-8")
            return {"success": True}

        self.assertIn("mcp_files_write_file", self.agent._tools_registry)

        original = CodeAgent.CONFIRMATION_REQUIRED_TOOLS
        CodeAgent.CONFIRMATION_REQUIRED_TOOLS = frozenset({"mcp_files_write_file"})
        try:
            result = self.execute_tool("mcp_files_write_file", {"path": str(canary)})
        finally:
            CodeAgent.CONFIRMATION_REQUIRED_TOOLS = original

        self.assertEqual(result.get("status"), "denied")
        self.assertEqual(ran, [], "MCP tool body ran despite being denied")
        self.assertFalse(canary.exists())

    def test_non_gated_tool_is_not_prompted(self):
        """Regression: ordinary tools run without a confirmation prompt."""
        ran = []

        @tool
        def harmless_probe(value: str) -> dict:
            """Non-gated tool used to prove normal execution is unchanged."""
            ran.append(value)
            return {"success": True, "echo": value}

        result = self.execute_tool("harmless_probe", {"value": "hello"})

        self.assertEqual(result, {"success": True, "echo": "hello"})
        self.assertEqual(ran, ["hello"])
        self.assertEqual(self.console.prompts, [])

    def test_unknown_tool_reports_error(self):
        """Unknown names still fail loudly (shape now matches _execute_tool)."""
        result = self.execute_tool("no_such_tool_xyz", {})

        self.assertEqual(result.get("status"), "error")
        self.assertIn("Unknown tool", result.get("error", ""))

    def test_long_running_tools_keep_generous_timeouts(self):
        """Tools with inner budgets above the 180s default need an override.

        Routing through ``_execute_tool`` wraps every tool in
        ``_call_tool_bounded``, whose default is ``DEFAULT_TOOL_TIMEOUT`` (180s).
        These tools drive npm/npx/prisma or looped LLM calls that legitimately
        run far longer, so without a ``@tool(timeout=...)`` override the guard
        abandons real work mid-flight.
        """
        for name, minimum in (
            ("run_cli_command", 1200),
            ("setup_nextjs_testing", 1200),
            ("manage_data_model", 1200),
            ("generate_style_tests", 600),
            ("fix_code", 1800),
        ):
            with self.subTest(tool=name):
                self.assertGreaterEqual(self.agent._resolve_tool_timeout(name), minimum)

    def test_every_checklist_tool_has_a_deliberate_timeout(self):
        """Inversion guard: a NEW slow checklist tool must not slip through.

        The per-tool list above can only re-state the tools we already fixed —
        which is exactly how ``fix_code`` was missed. This asserts instead that
        every tool the checklist can dispatch is *either* explicitly overridden
        *or* named here as known-fast, so adding a slow one fails this test
        until someone makes a decision about it.
        """
        # Tools that complete well inside the 180s default: pure file writes,
        # or subprocesses with inner budgets of 60s or less.
        KNOWN_FAST = {
            "setup_app_styling",
            "manage_api_endpoint",
            "manage_react_component",
            "update_landing_page",
            "validate_typescript",
            "validate_styles",
        }
        default = tool_execution_timeout()

        for template, name in sorted(TEMPLATE_TO_TOOL.items()):
            with self.subTest(template=template, tool=name):
                self.assertIn(
                    name,
                    self.agent._tools_registry,
                    f"{template} maps to unregistered tool {name}",
                )
                overridden = self.agent._resolve_tool_timeout(name) != default
                self.assertTrue(
                    overridden or name in KNOWN_FAST,
                    f"Tool '{name}' (template '{template}') has no @tool(timeout=...) "
                    f"override and is not listed as known-fast. Confirm it finishes "
                    f"within {default:g}s or give it an explicit override.",
                )


class TestRealGateReachesChecklist(unittest.TestCase):
    """End-to-end: the real gate's denial shape must reach the real executor.

    The other two suites each mock one half — one drives the real
    ``_execute_tool`` without a ChecklistExecutor, the other hand-writes the
    denial dict. If ``Agent._execute_tool`` ever renames its denial key, both
    stay green and the bypass silently returns. This pins the seam.
    """

    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.agent = CodeAgent(silent_mode=True, max_steps=5)
        self.agent._register_tools()
        self.agent.console = RecordingConsole(allow=False)

    def tearDown(self):
        if os.path.exists(self.test_dir):
            shutil.rmtree(self.test_dir, ignore_errors=True)
        _TOOL_REGISTRY.clear()

    def test_denial_flows_from_real_gate_into_checklist_result(self):
        executor = ChecklistExecutor(self.agent._create_tool_executor())
        checklist = GeneratedChecklist(
            items=[
                ChecklistItem("create_next_app", {"project_name": "app"}, "Create"),
                ChecklistItem("setup_prisma", {}, "Set up Prisma"),
            ],
            reasoning="Test",
        )

        result = executor.execute(checklist, make_context(self.test_dir))

        self.assertTrue(result.denied, "real denial did not reach the checklist")
        self.assertFalse(result.success)
        self.assertEqual(result.items_succeeded, 0)
        # Only the first item was attempted, and nothing was scaffolded.
        self.assertEqual(len(result.item_results), 1)
        self.assertFalse((Path(self.test_dir) / "node_modules").exists())
        self.assertEqual(
            [p[0] for p in self.agent.console.prompts], ["run_cli_command"]
        )


class TestDenialSurfacesThroughChecklist(unittest.TestCase):
    """A denial must read as a clear failure, not success and not a retry."""

    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.context = make_context(self.test_dir)
        self.checklist = GeneratedChecklist(
            items=[
                ChecklistItem("create_next_app", {"project_name": "app"}, "Create app")
            ],
            reasoning="Test",
        )

    def tearDown(self):
        if os.path.exists(self.test_dir):
            shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_denial_is_a_failure_not_a_silent_success(self):
        """`{"status": "denied"}` has no `success` key — it must not pass."""
        executor = ChecklistExecutor(
            lambda name, params: {
                "status": "denied",
                "error": "Tool 'run_cli_command' was denied by the user.",
            }
        )

        result = executor.execute(self.checklist, self.context)

        self.assertFalse(result.success)
        self.assertTrue(result.denied)
        self.assertEqual(result.items_succeeded, 0)
        self.assertEqual(result.items_failed, 1)
        self.assertTrue(any("denied" in e.lower() for e in result.errors))

    def test_error_status_is_also_not_a_silent_success(self):
        """`_execute_tool`'s error shape must not be read as success either."""
        executor = ChecklistExecutor(
            lambda name, params: {"status": "error", "error": "boom"}
        )

        result = executor.execute(self.checklist, self.context)

        self.assertFalse(result.success)
        self.assertFalse(result.denied)
        # The template name is prefixed so the replanning prompt knows which
        # item failed — _execute_tool's own messages omit the tool name.
        self.assertEqual(result.errors, ["[create_next_app] boom"])

    def test_denial_is_never_retried(self):
        """Recovery must not re-run a denied tool and re-prompt the user."""
        calls = []

        def denying_executor(name, params):
            calls.append(name)
            return {"status": "denied", "error": "denied by the user"}

        error_handler = MagicMock(spec=ErrorHandler)
        error_handler.handle_error.return_value = (RecoveryAction.RETRY, None)

        executor = ChecklistExecutor(denying_executor, error_handler=error_handler)
        item = ChecklistItem("create_next_app", {"project_name": "app"}, "Create app")

        result = executor._execute_item_with_recovery(item, self.context)

        self.assertFalse(result.success)
        self.assertTrue(result.denied)
        self.assertFalse(result.error_recoverable)
        self.assertEqual(len(calls), 1, "denied tool was retried")
        error_handler.handle_error.assert_not_called()

    def test_denial_stops_the_rest_of_the_checklist(self):
        """Later items must not run on top of a step the user refused."""
        calls = []

        def denying_executor(name, params):
            calls.append(name)
            return {"status": "denied", "error": "denied by the user"}

        checklist = GeneratedChecklist(
            items=[
                ChecklistItem("create_next_app", {"project_name": "app"}, "Create"),
                ChecklistItem("setup_prisma", {}, "Setup Prisma"),
            ],
            reasoning="Test",
        )
        executor = ChecklistExecutor(denying_executor)

        result = executor.execute(checklist, self.context)

        self.assertFalse(result.success)
        self.assertEqual(len(calls), 1, "checklist continued past a denial")

    def test_success_shape_still_passes(self):
        """Regression: the ordinary happy-path result is unchanged."""
        executor = ChecklistExecutor(
            lambda name, params: {"success": True, "files": ["a.ts"]}
        )

        result = executor.execute(self.checklist, self.context)

        self.assertTrue(result.success)
        self.assertFalse(result.denied)
        self.assertEqual(result.total_files, ["a.ts"])

    def test_bare_payload_dict_still_passes(self):
        """Regression: tools returning a plain payload still count as success."""
        executor = ChecklistExecutor(lambda name, params: {"files": ["b.ts"]})

        result = executor.execute(self.checklist, self.context)

        self.assertTrue(result.success)
        self.assertFalse(result.denied)


class TestOrchestratorStopsOnDenial(unittest.TestCase):
    """The outer checklist loop must not re-prompt for a denied tool."""

    def test_orchestrator_aborts_instead_of_replanning(self):
        from gaia_agent_code.orchestration.orchestrator import Orchestrator

        test_dir = tempfile.mkdtemp()
        try:
            calls = []

            def denying_executor(name, params):
                calls.append(name)
                return {"status": "denied", "error": "denied by the user"}

            llm = MagicMock()
            orchestrator = Orchestrator(
                tool_executor=denying_executor,
                llm_client=llm,
                max_checklist_loops=5,
            )
            checklist = GeneratedChecklist(
                items=[
                    ChecklistItem(
                        "create_next_app", {"project_name": "app"}, "Create app"
                    )
                ],
                reasoning="Test",
            )
            orchestrator.checklist_generator = MagicMock()
            orchestrator.checklist_generator.generate_initial_checklist.return_value = (
                checklist
            )
            orchestrator.checklist_generator.generate_debug_checklist.return_value = (
                checklist
            )
            orchestrator._prepare_project_directory = lambda ctx: test_dir
            orchestrator._assess_checkpoint = MagicMock()

            result = orchestrator.execute(make_context(test_dir))

            self.assertFalse(result.success)
            self.assertEqual(len(calls), 1, "orchestrator replanned after a denial")
            orchestrator._assess_checkpoint.assert_not_called()
            self.assertTrue(
                any("denied" in e.lower() for e in result.errors),
                f"denial not surfaced in errors: {result.errors}",
            )
        finally:
            shutil.rmtree(test_dir, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
