#!/usr/bin/env python
# Copyright(C) 2024-2025 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Code Agent for GAIA.

This agent provides intelligent code operations and assistance, focusing on
comprehensive Python support with capabilities for code understanding, generation,
modification, and validation.

"""

import json
import logging
import os
import time
from pathlib import Path
from typing import Any, Callable, Dict, Optional

from gaia.agents.base.agent import Agent
from gaia.agents.base.api_agent import ApiAgent
from gaia.agents.base.console import AgentConsole, SilentConsole
from gaia.agents.base.tools import _TOOL_REGISTRY
from gaia.security import PathValidator

from .orchestration import (
    ExecutionResult,
    Orchestrator,
    UserContext,
)
from .system_prompt import get_system_prompt
from .tools import (
    CodeFormattingMixin,
    CodeToolsMixin,
    ErrorFixingMixin,
    ExternalToolsMixin,
    FileIOToolsMixin,
    ProjectManagementMixin,
    TestingMixin,
    TypeScriptToolsMixin,
    ValidationAndParsingMixin,
    ValidationToolsMixin,
    WebToolsMixin,
)

# Import CLI tools
from .tools.cli_tools import CLIToolsMixin

# Import Prisma tools
from .tools.prisma_tools import PrismaToolsMixin

# Import refactored modules
from .validators import (
    AntipatternChecker,
    ASTAnalyzer,
    RequirementsValidator,
    SyntaxValidator,
)

logger = logging.getLogger(__name__)


class CodeAgent(
    ApiAgent,  # API support for VSCode integration
    Agent,
    CodeToolsMixin,  # Code generation, analysis, helpers
    ValidationAndParsingMixin,  # Validation, AST parsing, error fixing helpers
    FileIOToolsMixin,  # File I/O operations
    CodeFormattingMixin,  # Code formatting (Black, etc.)
    ProjectManagementMixin,  # Project/workspace management
    TestingMixin,  # Testing tools
    ErrorFixingMixin,  # Error fixing tools
    TypeScriptToolsMixin,  # TypeScript runtime tools (npm, template fetching, validation)
    WebToolsMixin,  # Next.js full-stack web development tools (replaces frontend/backend)
    PrismaToolsMixin,  # Prisma database setup and management
    CLIToolsMixin,  # Universal CLI execution with process management
    ExternalToolsMixin,  # Context7 and Perplexity integration for documentation and web search
    ValidationToolsMixin,  # Validation and testing tools
):
    """
    Intelligent autonomous code agent for comprehensive Python development workflows.

    This agent autonomously handles complex coding tasks including:
    - Workflow planning from requirements
    - Code generation with best practices
    - Automatic linting and formatting
    - Error detection and correction
    - Code execution and verification

    Usage:
        agent = CodeAgent()
        result = agent.process_query("Create a calculator app with error handling")
        # Agent will plan, generate, lint, fix, test, and verify automatically
    """

    def __init__(self, language="python", project_type="script", **kwargs):
        """Initialize the Code agent.

        Args:
            language: Programming language ('python' or 'typescript', default: 'python')
            project_type: Project type ('frontend', 'backend', 'fullstack', or 'script', default: 'script')
            **kwargs: Agent initialization parameters:
                - max_steps: Maximum conversation steps (default: 100)
                - model_id: LLM model to use (default: Qwen3-Coder-30B-A3B-Instruct-GGUF)
                - silent_mode: Suppress console output (default: False)
                - debug: Enable debug logging (default: False)
                - show_prompts: Display prompts sent to LLM (default: False)
                - streaming: Enable real-time LLM response streaming (default: False)
        """
        # Store language and project type for prompt selection
        self.language = language
        self.project_type = project_type

        # Default to more steps for complex workflows
        if "max_steps" not in kwargs:
            kwargs["max_steps"] = 100  # Increased for complex project generation
        # Use the coding model for better code understanding
        if "model_id" not in kwargs:
            kwargs["model_id"] = "Qwen3-Coder-30B-A3B-Instruct-GGUF"
        # Disable streaming by default (shows duplicate output)
        # Users can enable with --streaming flag if desired
        if "streaming" not in kwargs:
            kwargs["streaming"] = False
        # Code agent needs more plan iterations for complex projects
        if "max_plan_iterations" not in kwargs:
            kwargs["max_plan_iterations"] = 100

        # Ensure .gaia cache directory exists for temporary files
        self.cache_dir = Path.home() / ".gaia" / "cache"
        self.cache_dir.mkdir(parents=True, exist_ok=True)

        # Security: Configure allowed paths for file operations
        self.allowed_paths = kwargs.pop("allowed_paths", None)
        self.path_validator = PathValidator(self.allowed_paths)

        # Workspace root for API mode (passed from VSCode)
        self.workspace_root = None

        # Progress callback for real-time updates
        self.progress_callback = None

        super().__init__(**kwargs)

        # Store the tools description for later prompt reconstruction
        # (base Agent's __init__ already appended tools to self.system_prompt)
        self.tools_description = self._format_tools_for_prompt()

        # Initialize validators and analyzers
        self.syntax_validator = SyntaxValidator()
        self.antipattern_checker = AntipatternChecker()
        self.ast_analyzer = ASTAnalyzer()
        self.requirements_validator = RequirementsValidator()

        # Log context size requirement if not using cloud LLMs
        if not kwargs.get("use_claude") and not kwargs.get("use_chatgpt"):
            logger.debug(
                "Code Agent requires large context size (32768 tokens). "
                "Ensure Lemonade server is started with: lemonade-server serve --ctx-size 32768"
            )

    def _get_system_prompt(self, _user_input: Optional[str] = None) -> str:
        """Generate the system prompt for the Code agent.

        Uses the language and project_type set during initialization to
        select the appropriate prompt (no runtime detection).

        Args:
            _user_input: Optional user query (not used for detection anymore)

        Returns:
            str: System prompt for code operations
        """
        return get_system_prompt(language=self.language, project_type=self.project_type)

    def _create_console(self):
        """Create console for Code agent output.

        Returns:
            AgentConsole or SilentConsole: Console instance
        """
        if self.silent_mode:
            return SilentConsole()
        return AgentConsole()

    def _register_tools(self) -> None:
        """Register Code-specific tools from mixins."""
        # Register all tools from consolidated mixins
        self.register_code_tools()  # CodeToolsMixin
        self.register_file_io_tools()  # FileIOToolsMixin
        self.register_code_formatting_tools()  # CodeFormattingMixin
        self.register_project_management_tools()  # ProjectManagementMixin
        self.register_testing_tools()  # TestingMixin
        self.register_error_fixing_tools()  # ErrorFixingMixin
        self.register_typescript_tools()  # TypeScriptToolsMixin
        self.register_web_tools()  # WebToolsMixin (Next.js unified approach)
        self.register_prisma_tools()  # PrismaToolsMixin (Prisma database management)
        self.register_cli_tools()  # CLIToolsMixin (Universal CLI execution)
        self.register_external_tools()  # ExternalToolsMixin (Context7 & Perplexity)
        self.register_validation_tools()  # ValidationToolsMixin (Testing and validation)

    def process_query(
        self, user_input: str, workspace_root=None, progress_callback=None, **kwargs
    ):  # pylint: disable=arguments-differ,unused-argument
        """Process a query using the orchestrator workflow.

        Args:
            user_input: The user's query
            workspace_root: Optional workspace directory for file operations (from VSCode)
            progress_callback: Optional callback function for progress updates
            **kwargs: Additional arguments:
                - step_through: Enable step-through debugging (pause after each step)

        Returns:
            Execution result summary from the orchestrator
        """
        # Extract trace options
        trace = kwargs.get("trace", False)
        trace_filename = kwargs.get("filename")

        # Extract step_through from kwargs
        step_through = kwargs.get("step_through", False)

        del kwargs  # Unused - accept for CLI compatibility
        # Store workspace root and change to it if provided
        if workspace_root:
            self.workspace_root = workspace_root
            self.path_validator.add_allowed_path(workspace_root)
            original_cwd = os.getcwd()
            os.chdir(workspace_root)
            logger.debug(f"Changed working directory to: {workspace_root}")

        # Store progress callback for tools to use
        if progress_callback:
            self.progress_callback = progress_callback

        # Update system prompt based on actual user input for language detection
        # Reconstruct full prompt with language-specific base + tools
        base_prompt = self._get_system_prompt(user_input)

        # AI-powered schema inference (Perplexity -> Local LLM -> fallback)
        # This dynamically determines what fields the app needs without hardcoding
        schema_context = ""
        inferred_entity = None
        inferred_fields = None
        try:
            from .schema_inference import format_schema_context, infer_schema

            # Use self.chat for local LLM fallback if Perplexity unavailable
            chat_sdk = getattr(self, "chat", None)
            schema_result = infer_schema(user_input, chat_sdk)

            if schema_result.get("entity"):
                schema_context = format_schema_context(schema_result)
                inferred_entity = schema_result["entity"]
                # Convert fields from list format [{"name": "x", "type": "y"}]
                # to dict format {"x": "y"} expected by tools
                raw_fields = schema_result.get("fields", [])
                if isinstance(raw_fields, list):
                    inferred_fields = {
                        f["name"]: f.get("type", "string")
                        for f in raw_fields
                        if isinstance(f, dict) and "name" in f
                    }
                else:
                    inferred_fields = raw_fields
                logger.debug(
                    f"Schema inferred: {inferred_entity} "
                    f"({len(inferred_fields)} fields) via {schema_result['source']}"
                )
        except Exception as e:
            logger.warning(f"Schema inference failed (continuing without): {e}")

        # Add current working directory context
        workspace_context = ""
        if workspace_root:
            workspace_context = (
                f"\n\nProject directory (dedicated): {os.getcwd()}\n"
                f"IMPORTANT: When creating new projects (e.g., npx create-next-app, cargo new, etc.), "
                f"use '.' as the project name to install directly in this directory, NOT in a subdirectory.\n"
            )
        else:
            workspace_context = f"\n\nCurrent working directory: {os.getcwd()}\n"

        self.system_prompt = (
            base_prompt
            + schema_context  # AI-inferred schema (if available)
            + workspace_context
            + f"\n\n==== AVAILABLE TOOLS ====\n{self.tools_description}\n\n"
        )

        try:
            # Orchestrator is the ONLY workflow path
            # Handles correct step ordering for all project types
            execution_result = self._process_with_orchestrator(
                user_input,
                workspace_root,
                entity_name=inferred_entity,
                schema_fields=inferred_fields,
                step_through=step_through,
            )

            # Write trace to file if requested
            if trace:
                try:
                    # Construct trace data
                    trace_data = {
                        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
                        "agent": "CodeAgent",
                        "query": user_input,
                        "workspace_root": workspace_root or os.getcwd(),
                        "result": {
                            "success": execution_result.success,
                            "summary": execution_result.summary,
                            "outputs": execution_result.outputs,
                            "errors": execution_result.errors,
                        },
                    }

                    if not trace_filename:
                        timestamp = time.strftime("%Y%m%d_%H%M%S")
                        trace_filename = f"agent_trace_{timestamp}.json"

                    # Write to file
                    with open(trace_filename, "w", encoding="utf-8") as f:
                        json.dump(trace_data, f, indent=2)

                    logger.info(f"Trace written to {trace_filename}")
                    if not self.silent_mode:
                        self.console.print(f"\nTrace written to {trace_filename}")

                except Exception as e:
                    logger.error(f"Failed to write trace file: {e}")

            # Return dict matching app.py's expected format
            project_dir = workspace_root or os.getcwd()
            return {
                "status": "success" if execution_result.success else "error",
                "result": execution_result.summary,
                "phases_completed": execution_result.phases_completed,
                "phases_failed": execution_result.phases_failed,
                "steps_succeeded": execution_result.steps_succeeded,
                "steps_failed": execution_result.steps_failed,
                "errors": execution_result.errors,
                "project_dir": project_dir,
            }
        finally:
            # Restore original working directory if we changed it
            if workspace_root:
                os.chdir(original_cwd)
                logger.info(f"Restored working directory to: {original_cwd}")

    def _create_tool_executor(self) -> Callable[[str, Dict[str, Any]], Any]:
        """Create a tool executor function that uses registered tools.

        Returns:
            Function that executes tools by name
        """

        def execute_tool(tool_name: str, tool_args: Dict[str, Any]) -> Any:
            """Execute a registered tool."""
            if tool_name not in _TOOL_REGISTRY:
                return {"success": False, "error": f"Unknown tool: {tool_name}"}

            tool_func = _TOOL_REGISTRY[tool_name]["function"]
            try:
                return tool_func(**tool_args)
            except Exception as e:  # pylint: disable=broad-exception-caught
                logger.exception(f"Tool execution failed: {tool_name}")
                return {"success": False, "error": str(e)}

        return execute_tool

    def _process_with_orchestrator(
        self,
        user_input: str,
        workspace_root: Optional[str] = None,
        entity_name: Optional[str] = None,
        schema_fields: Optional[Dict[str, str]] = None,
        step_through: bool = False,
    ) -> ExecutionResult:
        """Process request using the LLM-driven orchestrator.

        Args:
            user_input: User's request
            workspace_root: Optional workspace directory
            entity_name: Entity name from schema inference (e.g., "Todo")
            schema_fields: Field definitions from schema inference
            step_through: Enable step-through debugging

        Returns:
            ExecutionResult with workflow execution status

        Raises:
            ValueError: If no LLM client (chat) is available
        """
        tool_executor = self._create_tool_executor()

        # Create user context with inferred schema
        context = UserContext(
            user_request=user_input,
            project_dir=workspace_root or os.getcwd(),
            language=self.language,
            project_type=self.project_type,
            entity_name=entity_name,
            schema_fields=schema_fields,
        )

        # Create LLM fixer wrapper that adapts signature
        # ErrorHandler expects (error_text, code) -> Optional[fixed_code]
        # _fix_code_with_llm expects (code, file_path, error_msg) -> Optional[fixed_code]
        def llm_fixer(error_text: str, code: str) -> Optional[str]:
            """Wrapper to adapt _fix_code_with_llm signature for ErrorHandler."""
            return self._fix_code_with_llm(code, "file.ts", error_text)

        # Get LLM client for checklist generation (required)
        # The chat SDK has a send(message, timeout) method compatible with ChatSDK protocol
        llm_client = getattr(self, "chat", None)
        if llm_client is None:
            raise ValueError(
                "LLM client (chat) is required for orchestrator. "
                "Ensure the agent has a chat SDK configured."
            )

        orchestrator = Orchestrator(
            tool_executor=tool_executor,
            llm_client=llm_client,
            llm_fixer=llm_fixer,
            progress_callback=self._orchestrator_progress_callback,
            console=self.console,
        )

        logger.debug("Running LLM-driven orchestrator")
        return orchestrator.execute(context, step_through=step_through)

    def _orchestrator_progress_callback(
        self, phase: str, step: str, current: int, total: int
    ) -> None:
        """Handle progress updates from orchestrator."""
        if self.progress_callback:
            self.progress_callback(
                {
                    "type": "progress",
                    "phase": phase,
                    "step": step,
                    "current": current,
                    "total": total,
                }
            )
        # Also print to console if not silent
        # Skip "checklist" phase printing as ChecklistExecutor handles its own output
        if not self.silent_mode and hasattr(self, "console") and phase != "checklist":
            self.console.print_info(f"[{current}/{total}] {phase}: {step}")

    def display_result(
        self,
        title: str = "Result",
        result: Dict[str, Any] = None,
        print_result: bool = False,
    ) -> None:
        """Display orchestrator execution result with a nice summary.

        Args:
            title: Title for the result display
            result: Orchestrator result dictionary
            print_result: If True, also print raw JSON
        """
        if result is None:
            self.console.print_warning("No result available to display.")
            return

        # Print raw JSON if requested
        if print_result:
            self.console.pretty_print_json(result, title)
            return

        # Build a nice summary for orchestrator results
        status = result.get("status", "unknown")
        phases_completed = result.get("phases_completed", [])
        phases_failed = result.get("phases_failed", [])
        steps_succeeded = result.get("steps_succeeded", 0)
        steps_failed = result.get("steps_failed", 0)
        errors = result.get("errors", [])

        self.console.print("")  # Blank line before summary

        # Status banner
        if status == "success":
            self.console.print("=" * 60)
            self.console.print_success("  PROJECT GENERATION COMPLETE")
            self.console.print("=" * 60)
        else:
            self.console.print("=" * 60)
            self.console.print_warning("  PROJECT GENERATION FINISHED WITH ISSUES")
            self.console.print("=" * 60)

        self.console.print("")

        # Phase summary
        if phases_completed:
            self.console.print(f"Phases completed: {', '.join(phases_completed)}")
        if phases_failed:
            self.console.print(f"Phases failed: {', '.join(phases_failed)}")

        # Step summary
        total_steps = steps_succeeded + steps_failed
        self.console.print(f"Steps: {steps_succeeded}/{total_steps} succeeded")

        # Errors/warnings
        if errors:
            self.console.print("")
            self.console.print("Warnings/Errors:")
            for error in errors[:5]:  # Show first 5 errors
                self.console.print(f"  - {error}")
            if len(errors) > 5:
                self.console.print(f"  ... and {len(errors) - 5} more")

        self.console.print("")

        # Next steps
        if status == "success":
            project_dir = result.get("project_dir", os.getcwd())
            self.console.print("Next steps:")
            self.console.print(f"  1. cd {project_dir}")
            self.console.print("  2. npm run dev")
            self.console.print("  3. Open http://localhost:3000 in your browser")
        else:
            self.console.print("Next steps:")
            self.console.print("  1. Review the errors above")
            self.console.print("  2. Run the command again to retry failed steps")

        self.console.print("")
        self.console.print("=" * 60)


def main():
    """Main entry point for testing."""
    agent = CodeAgent()
    print("CodeAgent initialized successfully")
    print(f"Cache directory: {agent.cache_dir}")
    print(
        "Validators: syntax_validator, antipattern_checker, ast_analyzer, "
        "requirements_validator"
    )


if __name__ == "__main__":
    main()
