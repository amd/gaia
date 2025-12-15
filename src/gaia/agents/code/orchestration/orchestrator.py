# Copyright(C) 2024-2025 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Orchestrator for LLM-driven workflow execution.

The Orchestrator controls workflow execution using Checklist Mode:
- LLM generates a checklist of template invocations based on user request
- Executor runs templates deterministically with error recovery
- Provides semantic understanding (e.g., adds checkboxes for todos)

Features:
- LLM-driven checklist generation
- Deterministic template execution
- Error recovery with three-tier strategy
- Progress reporting
"""

import json
import logging
import re
import subprocess
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Protocol

from gaia.agents.base.console import AgentConsole

from .steps.base import ToolExecutor, UserContext
from .steps.error_handler import ErrorHandler

logger = logging.getLogger(__name__)


def _estimate_token_count(text: str) -> int:
    """Lightweight token estimate assuming ~4 characters per token."""
    avg_chars_per_token = 4
    byte_length = len(text.encode("utf-8"))
    return max(1, (byte_length + avg_chars_per_token - 1) // avg_chars_per_token)


class ChatSDK(Protocol):
    """Protocol for chat SDK interface used by checklist generator."""

    def send(self, message: str, timeout: int = 600, no_history: bool = False) -> Any:
        """Send a message and get response."""
        ...


@dataclass
class ExecutionResult:
    """Result of a complete workflow execution."""

    success: bool
    phases_completed: List[str] = field(default_factory=list)
    phases_failed: List[str] = field(default_factory=list)
    total_steps: int = 0
    steps_succeeded: int = 0
    steps_failed: int = 0
    steps_skipped: int = 0
    errors: List[str] = field(default_factory=list)
    outputs: Dict[str, Any] = field(default_factory=dict)

    @property
    def summary(self) -> str:
        """Get a human-readable summary."""
        status = "SUCCESS" if self.success else "FAILED"
        return (
            f"{status}: {self.steps_succeeded}/{self.total_steps} steps completed, "
            f"{self.steps_failed} failed, {self.steps_skipped} skipped"
        )


CHECKPOINT_REVIEW_PROMPT = """You are the checkpoint reviewer for the GAIA web development agent.

You receive:
- The original user request
- A summary of the latest checklist execution (including errors/warnings)
- Logs from the validation and testing tools (run_typescript_check, validate_styles, run_tests, etc.)
- Any previously requested fixes that are still outstanding

Decide if the application is ready to ship or if additional fixes are required.

Rules:
1. If ANY validation or test log failed, status must be \"needs_fix\" with concrete guidance.
2. Only return \"complete\" when the app works end-to-end and validations passed.
3. When fixes are needed, suggest actionable steps that can be executed through `fix_code` (LLM-assisted repair of problematic files).

Respond with concise JSON only:
{
  \"status\": \"complete\" | \"needs_fix\",
  \"reasoning\": \"short justification\",
  \"issues\": [\"list of concrete bugs or failures\"],
  \"fix_instructions\": [\"ordered actions the next checklist should perform\"]
}
"""

MAX_CHAT_HISTORY_TOKENS = 15000


@dataclass
class CheckpointAssessment:
    """LLM-produced verdict about the current checkpoint."""

    status: str
    reasoning: str
    issues: List[str] = field(default_factory=list)
    fix_instructions: List[str] = field(default_factory=list)

    @property
    def needs_fix(self) -> bool:
        """Return True when the reviewer requires another checklist."""
        return self.status.lower() != "complete"

    def to_dict(self) -> Dict[str, Any]:
        """Serialize the assessment."""
        return {
            "status": self.status,
            "reasoning": self.reasoning,
            "issues": self.issues,
            "fix_instructions": self.fix_instructions,
        }


class Orchestrator:
    """Controls LLM-driven workflow execution with error recovery.

    The orchestrator uses Checklist Mode exclusively:
    - LLM analyzes user request and generates a checklist of templates
    - Executor runs templates deterministically
    - Provides semantic understanding (e.g., adds checkboxes for todos)
    """

    def __init__(
        self,
        tool_executor: ToolExecutor,
        llm_client: ChatSDK,
        llm_fixer: Optional[Callable[[str, str], Optional[str]]] = None,
        progress_callback: Optional[Callable[[str, str, int, int], None]] = None,
        console: Optional[AgentConsole] = None,
        max_checklist_loops: int = 10,
    ):
        """Initialize orchestrator.

        Args:
            tool_executor: Function to execute tools (name, args) -> result
            llm_client: Chat SDK for checklist generation (required)
            llm_fixer: Optional LLM-based code fixer for escalation
            progress_callback: Optional callback(phase, step, current, total)
            console: Optional console for displaying output
            max_checklist_loops: Max number of checklist iterations before giving up
        """
        if llm_client is None:
            raise ValueError("llm_client is required for Orchestrator")

        self.tool_executor = tool_executor
        self.llm_client = llm_client
        self.error_handler = ErrorHandler(
            command_executor=self._run_command,
            llm_fixer=llm_fixer,
        )
        self.progress_callback = progress_callback
        self.console = console
        self.max_checklist_loops = max(1, max_checklist_loops)

        # Initialize checklist components
        from .checklist_executor import ChecklistExecutor
        from .checklist_generator import ChecklistGenerator

        self.checklist_generator = ChecklistGenerator(llm_client)
        self.checklist_executor = ChecklistExecutor(
            tool_executor,
            llm_client=llm_client,  # Pass LLM for per-item code generation
            error_handler=self.error_handler,
            progress_callback=self._checklist_progress_callback,
            console=console,  # Pass console
        )
        logger.debug(
            "Orchestrator initialized - LLM will plan execution AND generate code per item"
        )

    def execute(
        self, context: UserContext, step_through: bool = False
    ) -> ExecutionResult:
        """Execute the workflow using iterative LLM-generated checklists."""
        logger.debug("Executing workflow (LLM-driven checklist loop)")

        from .project_analyzer import ProjectAnalyzer

        analyzer = ProjectAnalyzer()
        aggregated_validation_logs: List[Any] = []
        fix_feedback: List[str] = []
        iteration_outputs: List[Dict[str, Any]] = []
        combined_errors: List[str] = []
        previous_execution_errors: List[str] = []
        previous_validation_logs: List[Any] = []

        total_steps = 0
        steps_succeeded = 0
        steps_failed = 0
        success = False

        for iteration in range(1, self.max_checklist_loops + 1):
            logger.debug("Starting checklist iteration %d", iteration)

            if iteration > 1:
                summary_result = self._maybe_summarize_conversation_history()
                if summary_result and self.console:
                    self.console.print_info(
                        "Conversation history summarized to stay within token limits."
                    )

            project_state = analyzer.analyze(context.project_dir)

            # Surface accumulated signals to the next checklist prompt
            context.validation_reports = [
                log.to_dict() for log in aggregated_validation_logs
            ]
            context.fix_feedback = fix_feedback.copy()

            logger.info(
                "Generating checklist iteration %d of %d",
                iteration,
                self.max_checklist_loops,
            )
            if self.console:
                self.console.print_info(
                    f"Generating checklist iteration {iteration} of {self.max_checklist_loops}"
                )
            if iteration == 1:
                checklist = self.checklist_generator.generate_initial_checklist(
                    context, project_state
                )
            else:
                checklist = self.checklist_generator.generate_debug_checklist(
                    context=context,
                    project_state=project_state,
                    prior_errors=previous_execution_errors,
                    validation_logs=previous_validation_logs,
                )

            if not checklist.is_valid:
                logger.error(
                    "Invalid checklist (iteration %d): %s",
                    iteration,
                    checklist.validation_errors,
                )
                try:
                    checklist_dump = json.dumps(checklist.to_dict(), indent=2)
                except Exception:  # pylint: disable=broad-exception-caught
                    checklist_dump = str(checklist)
                logger.error("Invalid checklist payload: %s", checklist_dump)
                if self.console:
                    self.console.pretty_print_json(
                        checklist.to_dict(), title="Invalid Checklist"
                    )
                combined_errors.extend(checklist.validation_errors)
                assessment = CheckpointAssessment(
                    status="needs_fix",
                    reasoning="Checklist validation failed",
                    issues=checklist.validation_errors.copy(),
                    fix_instructions=checklist.validation_errors.copy(),
                )
                iteration_outputs.append(
                    {
                        "iteration": iteration,
                        "checklist": checklist.to_dict(),
                        "execution": None,
                        "assessment": assessment.to_dict(),
                    }
                )
                break

            logger.debug(
                "Generated checklist with %d items: %s",
                len(checklist.items),
                checklist.reasoning,
            )

            checklist_result = self.checklist_executor.execute(
                checklist, context, step_through=step_through
            )

            total_steps += len(checklist_result.item_results)
            steps_succeeded += checklist_result.items_succeeded
            steps_failed += checklist_result.items_failed
            combined_errors.extend(checklist_result.errors)

            aggregated_validation_logs.extend(checklist_result.validation_logs)
            previous_execution_errors = checklist_result.errors.copy()
            previous_validation_logs = checklist_result.validation_logs.copy()

            logger.info("Assessing application state after iteration %d", iteration)
            if self.console:
                self.console.print_info(
                    f"Assessing application state after iteration {iteration}"
                )
            assessment = self._assess_checkpoint(
                context=context,
                checklist=checklist,
                execution_result=checklist_result,
                validation_history=aggregated_validation_logs,
            )
            if assessment.needs_fix:
                logger.info(
                    "Application not ready after iteration %d, planning another checklist: %s",
                    iteration,
                    assessment.reasoning or "no reasoning provided",
                )
                if self.console:
                    self.console.print_info(
                        "Application not ready; preparing another checklist."
                    )
            else:
                logger.info(
                    "Application marked complete after iteration %d: %s",
                    iteration,
                    assessment.reasoning or "no reasoning provided",
                )
                if self.console:
                    self.console.print_success("Application marked complete.")

            iteration_outputs.append(
                {
                    "iteration": iteration,
                    "checklist": checklist.to_dict(),
                    "execution": {
                        "summary": checklist_result.summary,
                        "success": checklist_result.success,
                        "files": checklist_result.total_files,
                        "errors": checklist_result.errors,
                        "warnings": checklist_result.warnings,
                        "item_results": [
                            r.to_dict() for r in checklist_result.item_results
                        ],
                        "validation_logs": [
                            log.to_dict() for log in checklist_result.validation_logs
                        ],
                    },
                    "assessment": assessment.to_dict(),
                }
            )

            if not assessment.needs_fix:
                success = (
                    checklist_result.success and assessment.status.lower() == "complete"
                )
                break

            instructions = assessment.fix_instructions or assessment.issues
            if not instructions and assessment.reasoning:
                instructions = [assessment.reasoning]
            if instructions:
                fix_feedback.extend(instructions)

        else:
            combined_errors.append(
                f"Reached maximum checklist iterations ({self.max_checklist_loops}) without passing validation"
            )

        latest_execution = None
        latest_checklist = None
        if iteration_outputs:
            latest_entry = iteration_outputs[-1]
            latest_execution = latest_entry.get("execution")
            latest_checklist = latest_entry.get("checklist")

        outputs = {
            "iterations": iteration_outputs,
            "validation_logs": [log.to_dict() for log in aggregated_validation_logs],
            "fix_feedback": fix_feedback,
        }

        if latest_execution:
            outputs["files"] = latest_execution.get("files", [])
            outputs["detailed_results"] = latest_execution.get("item_results", [])
        if latest_checklist:
            outputs["checklist"] = latest_checklist

        return ExecutionResult(
            success=success,
            phases_completed=["checklist"] if success else [],
            phases_failed=[] if success else ["checklist"],
            total_steps=total_steps,
            steps_succeeded=steps_succeeded,
            steps_failed=steps_failed,
            steps_skipped=0,
            errors=combined_errors,
            outputs=outputs,
        )

    def _run_command(self, command: str, cwd: Optional[str] = None) -> tuple[int, str]:
        """Run a shell command.

        Args:
            command: Command to run
            cwd: Working directory

        Returns:
            Tuple of (exit_code, output)
        """
        try:
            result = subprocess.run(
                command,
                shell=True,
                cwd=cwd,
                capture_output=True,
                text=True,
                timeout=1200,
                check=False,  # We handle return codes ourselves
            )
            output = result.stdout + result.stderr
            return result.returncode, output
        except subprocess.TimeoutExpired:
            return 1, "Command timed out"
        except Exception as e:
            return 1, str(e)

    def _checklist_progress_callback(
        self, description: str, current: int, total: int
    ) -> None:
        """Progress callback adapter for checklist execution.

        Converts checklist progress format to the standard progress format.

        Args:
            description: Current item description
            current: Current item number
            total: Total items
        """
        if self.progress_callback:
            self.progress_callback("checklist", description, current, total)

    def _assess_checkpoint(
        self,
        context: UserContext,
        checklist: Any,
        execution_result: Any,
        validation_history: List[Any],
    ) -> CheckpointAssessment:
        """Ask the LLM whether the workflow is complete or needs another checklist."""
        prompt = self._build_checkpoint_prompt(
            context=context,
            checklist=checklist,
            execution_result=execution_result,
            validation_history=validation_history,
        )

        try:
            response = self.llm_client.send(prompt, timeout=1200, no_history=True)
            data = self._parse_checkpoint_response(response)
            return CheckpointAssessment(
                status=data.get("status", "needs_fix"),
                reasoning=data.get("reasoning", ""),
                issues=data.get("issues", []),
                fix_instructions=data.get("fix_instructions", []),
            )
        except Exception as exc:  # pylint: disable=broad-exception-caught
            logger.exception("Checkpoint assessment failed")
            return CheckpointAssessment(
                status="needs_fix",
                reasoning="Failed to interpret checkpoint reviewer output",
                issues=[f"Checkpoint reviewer error: {exc}"],
                fix_instructions=[
                    "Inspect validation logs, then fix the root cause using fix_code."
                ],
            )

    def _build_checkpoint_prompt(
        self,
        context: UserContext,
        checklist: Any,
        execution_result: Any,
        validation_history: List[Any],
    ) -> str:
        """Build the prompt for the checkpoint reviewer."""
        validation_summary = self._format_validation_history(validation_history)

        outstanding = (
            "\n".join(f"- {item}" for item in context.fix_feedback)
            if context.fix_feedback
            else "None"
        )

        errors = execution_result.errors or ["None"]
        warnings = execution_result.warnings or []

        sections = [
            CHECKPOINT_REVIEW_PROMPT.strip(),
            "",
            "## User Request",
            context.user_request,
            "",
            "## Latest Checklist Plan",
            f"Reasoning: {checklist.reasoning}",
            "",
            "## Execution Summary",
            execution_result.summary,
            "",
            "## Execution Errors",
            "\n".join(f"- {err}" for err in errors),
            "",
            "## Execution Warnings",
            "\n".join(f"- {warn}" for warn in warnings) if warnings else "None",
            "",
            "## Validation & Test Logs",
            validation_summary,
            "",
            "## Outstanding Fix Requests",
            outstanding,
        ]

        return "\n".join(sections)

    def _maybe_summarize_conversation_history(self) -> Optional[str]:
        """Trigger ChatSDK conversation summarization when available."""
        chat_sdk = getattr(self, "llm_client", None)
        if not chat_sdk or not hasattr(chat_sdk, "summarize_conversation_history"):
            return None

        try:
            summary = chat_sdk.summarize_conversation_history(
                max_history_tokens=MAX_CHAT_HISTORY_TOKENS
            )
            if summary:
                logger.info(
                    "Conversation history summarized to ~%d tokens",
                    _estimate_token_count(summary),
                )
            return summary
        except Exception as exc:  # pylint: disable=broad-exception-caught
            logger.exception("Failed to summarize conversation history: %s", exc)
            return None

    def _format_validation_history(self, validation_history: List[Any]) -> str:
        """Format validation logs for the checkpoint prompt."""
        if not validation_history:
            return "No validation or test commands have been executed yet."

        lines: List[str] = []
        recent = validation_history[-5:]
        for entry in recent:
            if hasattr(entry, "to_dict"):
                entry_data = entry.to_dict()
            elif isinstance(entry, dict):
                entry_data = entry
            else:
                entry_data = {}

            template = getattr(entry, "template", None) or entry_data.get("template")
            description = getattr(entry, "description", None) or entry_data.get(
                "description", ""
            )
            success = getattr(entry, "success", None)
            if success is None:
                success = entry_data.get("success", True)
            status = "PASS" if success else "FAIL"
            error = getattr(entry, "error", None) or entry_data.get("error")
            output = getattr(entry, "output", None) or entry_data.get("output", {})

            lines.append(f"- [{status}] {template}: {description}")
            if error:
                lines.append(f"  Error: {error}")

            snippet = ""
            if isinstance(output, dict):
                for key in ("stdout", "stderr", "message", "log", "details"):
                    if output.get(key):
                        snippet = str(output[key])
                        break
                if not snippet and output:
                    snippet = json.dumps(output)[:400]
            elif output:
                snippet = str(output)[:400]

            snippet = snippet.strip()
            if snippet:
                lines.append(f"  Output: {snippet[:400]}")

        return "\n".join(lines)

    def _parse_checkpoint_response(self, response: Any) -> Dict[str, Any]:
        """Parse JSON output from the checkpoint reviewer."""
        text = self._extract_response_text(response)
        json_str = self._extract_json(text)
        return json.loads(json_str)

    @staticmethod
    def _extract_response_text(response: Any) -> str:
        """Normalize SDK response objects to raw text."""
        if isinstance(response, str):
            return response
        if hasattr(response, "text"):
            return response.text
        if hasattr(response, "content"):
            return response.content
        if isinstance(response, dict):
            return response.get("text", response.get("content", str(response)))
        return str(response)

    @staticmethod
    def _extract_json(text: str) -> str:
        """Extract JSON blob from arbitrary text (markdown-safe)."""
        code_block = re.search(r"```(?:json)?\\s*\\n?(.*?)\\n?```", text, re.DOTALL)
        if code_block:
            return code_block.group(1).strip()

        json_match = re.search(r"\\{.*\\}", text, re.DOTALL)
        if json_match:
            return json_match.group(0)

        return text.strip()
