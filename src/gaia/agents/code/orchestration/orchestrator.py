# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
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
import os
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Protocol

from gaia.agents.base.console import AgentConsole

from .steps.base import ToolExecutor, UserContext
from .steps.error_handler import ErrorHandler

logger = logging.getLogger(__name__)


class ProjectDirectoryError(Exception):
    """Raised when the project directory cannot be prepared safely."""


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

        try:
            context.project_dir = self._prepare_project_directory(context)
        except ProjectDirectoryError as exc:
            error_message = str(exc)
            logger.error(error_message)
            if self.console:
                self.console.print_error(error_message)
            return ExecutionResult(
                success=False,
                phases_completed=[],
                phases_failed=["project_directory"],
                total_steps=1,
                steps_succeeded=0,
                steps_failed=1,
                steps_skipped=0,
                errors=[error_message],
                outputs={
                    "iterations": [],
                    "validation_logs": [],
                    "fix_feedback": [],
                    "project_dir": context.project_dir,
                },
            )

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
            "project_dir": context.project_dir,
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
            response = self.llm_client.send(prompt, timeout=1200)
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
        validation_summary = self._format_validation_history(
            validation_history, getattr(execution_result, "validation_logs", None)
        )

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

    def _prepare_project_directory(self, context: UserContext) -> str:
        """
        Ensure the project directory is ready for creation workflows.

        If the provided path exists and is non-empty without an existing project,
        pick a unique subdirectory via the LLM to avoid create-next-app failures.
        """
        base_path = Path(context.project_dir).expanduser()
        if base_path.exists() and not base_path.is_dir():
            raise ProjectDirectoryError(
                f"Provided path is not a directory: {base_path}"
            )

        if not base_path.exists():
            base_path.mkdir(parents=True, exist_ok=True)
            logger.info("Created project directory: %s", base_path)
            return str(base_path)

        existing_entries = [p.name for p in base_path.iterdir()]
        if not existing_entries:
            return str(base_path)

        if self.console:
            self.console.print_warning(
                f"Target directory {base_path} is not empty; selecting a new subdirectory."
            )

        suggested = self._choose_subdirectory_name(
            base_path, existing_entries, context.user_request
        )
        if not suggested:
            raise ProjectDirectoryError(
                f"Unable to find an available project name under {base_path}. "
                "Provide one explicitly with --path."
            )

        new_dir = base_path / suggested
        new_dir.mkdir(parents=False, exist_ok=False)
        logger.info("Using nested project directory: %s", new_dir)
        # Align process cwd with the newly created project directory.
        try:
            os.chdir(new_dir)
        except OSError as exc:
            logger.warning("Failed to chdir to %s: %s", new_dir, exc)
        if self.console:
            self.console.print_info(f"Using project directory: {new_dir}")
        return str(new_dir)

    def _choose_subdirectory_name(
        self, base_path: Path, existing_entries: List[str], user_request: str
    ) -> Optional[str]:
        """Ask the LLM for a unique subdirectory name, retrying on conflicts."""
        existing_lower = {name.lower() for name in existing_entries}
        prompt = self._build_directory_prompt(
            base_path, existing_entries, user_request, None
        )
        last_reason = None

        system_prompt = "You suggest concise folder names for new projects."

        for attempt in range(1, 4):
            try:
                response = self._send_prompt_without_history(
                    prompt, timeout=120, system_prompt=system_prompt
                )
            except Exception as exc:  # pylint: disable=broad-exception-caught
                last_reason = f"LLM error on attempt {attempt}: {exc}"
                logger.warning(last_reason)
                prompt = self._build_directory_prompt(
                    base_path, existing_entries, user_request, last_reason
                )
                continue

            raw_response = self._extract_response_text(response)
            candidate = self._sanitize_directory_name(raw_response)
            if not candidate:
                last_reason = "LLM returned an empty or invalid directory name."
            elif candidate.lower() in existing_lower:
                last_reason = f"Name '{candidate}' already exists in {base_path}."
            elif "/" in candidate or "\\" in candidate or ".." in candidate:
                last_reason = "Directory name contained path separators or traversal."
            elif len(candidate) > 64:
                last_reason = "Directory name exceeded 64 characters."
            else:
                candidate_path = base_path / candidate
                if candidate_path.exists():
                    last_reason = f"Directory '{candidate}' already exists."
                else:
                    return candidate

            logger.warning(
                "Directory name attempt %d rejected: %s", attempt, last_reason
            )
            prompt = self._build_directory_prompt(
                base_path, existing_entries, user_request, last_reason
            )

        return None

    @staticmethod
    def _sanitize_directory_name(raw: str) -> str:
        """Normalize LLM output to a filesystem-safe directory name."""
        if not raw:
            return ""
        candidate = raw.strip().strip("`'\"")
        candidate = candidate.splitlines()[0].strip()
        candidate = re.sub(r"[^A-Za-z0-9_-]+", "-", candidate)
        return candidate.strip("-_").lower()

    def _send_prompt_without_history(
        self, prompt: str, timeout: int = 120, system_prompt: Optional[str] = None
    ) -> Any:
        """
        Send a prompt without reading from or writing to chat history.

        Prefers the underlying LLM client's `generate` API when available,
        falling back to `send(..., no_history=True)` for compatibility.
        """
        # If the ChatSDK exposes the underlying LLM client, use it directly with chat messages
        # to avoid any stored history and ensure system prompts are applied cleanly.
        llm_client = getattr(self.llm_client, "llm_client", None)
        if llm_client and hasattr(llm_client, "generate"):
            model = getattr(getattr(self.llm_client, "config", None), "model", None)
            messages = []
            if system_prompt:
                messages.append({"role": "system", "content": system_prompt})
            messages.append({"role": "user", "content": prompt})
            return llm_client.generate(
                prompt=prompt,
                messages=messages,
                model=model,
                timeout=timeout,
                endpoint="chat",
            )

        # Fallback: use send with no_history to avoid persisting messages.
        if hasattr(self.llm_client, "send"):
            return self.llm_client.send(
                prompt, timeout=timeout, no_history=True, system_prompt=system_prompt
            )

        raise ValueError("LLM client does not support generate or send APIs")

    @staticmethod
    def _build_directory_prompt(
        base_path: Path,
        existing_entries: List[str],
        user_request: Optional[str],
        rejection_reason: Optional[str],
    ) -> str:
        """Construct the LLM prompt for picking a safe project subdirectory."""
        entries = sorted(existing_entries)
        max_list = 50
        if len(entries) > max_list:
            entries_display = "\n".join(f"- {name}" for name in entries[:max_list])
            entries_display += f"\n- ...and {len(entries) - max_list} more"
        else:
            entries_display = "\n".join(f"- {name}" for name in entries)

        prompt_sections = [
            "You must choose a new folder name for a project because the target path is not empty.",
            f"Base path: {base_path}",
            "Existing files and folders you MUST avoid (do not reuse any of these names):",
            entries_display or "- <empty>",
            "User request driving this project:",
            user_request or "<no request provided>",
            "Rules:",
            "- Return a single folder name only. Do NOT echo the instructions. No paths, quotes, JSON, or extra text.",
            "- Use lowercase kebab-case or snake_case; ASCII letters, numbers, hyphens, and underscores only.",
            "- Do not use any existing names above. Avoid dots, spaces, or slashes.",
            "- Keep it under 40 characters.",
        ]

        if rejection_reason:
            prompt_sections.append(
                f"Previous suggestion was rejected: {rejection_reason}. Try a different unique name."
            )

        return "\n".join(prompt_sections)

    def _format_validation_history(
        self, validation_history: List[Any], latest_plan_logs: Optional[List[Any]]
    ) -> str:
        """Format validation logs, splitting latest plan from historical ones."""

        if not validation_history:
            return "No validation or test commands have been executed yet."

        latest_logs = latest_plan_logs or []
        latest_count = len(latest_logs)
        historical_logs = (
            validation_history[:-latest_count] if latest_count else validation_history
        )

        def normalize(entry: Any) -> Dict[str, Any]:
            if hasattr(entry, "to_dict"):
                return entry.to_dict()
            if isinstance(entry, dict):
                return entry
            return {}

        def render(entries: List[Any], limit: Optional[int] = None) -> List[str]:
            if not entries:
                return ["None"]

            selected = entries if limit is None else entries[-limit:]
            lines: List[str] = []
            for entry in selected:
                data = normalize(entry)
                template = data.get("template", "unknown")
                description = data.get("description", "")
                success = data.get("success", True)
                status = "PASS" if success else "FAIL"
                error = data.get("error")
                output = data.get("output", {})

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
            return lines

        sections: List[str] = []
        sections.append("### Latest Plan Results")
        sections.extend(render(list(latest_logs)))
        sections.append("")
        sections.append("### Previous Plan History")
        sections.extend(render(list(historical_logs), limit=5))

        return "\n".join(sections).strip()

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
