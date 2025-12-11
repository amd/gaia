#!/usr/bin/env python
# Copyright(C) 2024-2025 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Code Agent for GAIA.

This agent provides intelligent code operations and assistance, focusing on
comprehensive Python support with capabilities for code understanding, generation,
modification, and validation.

"""

import logging
import os
from pathlib import Path
from typing import Optional

from gaia.agents.base.agent import Agent
from gaia.agents.base.api_agent import ApiAgent
from gaia.agents.base.console import AgentConsole, SilentConsole
from gaia.security import PathValidator

from .system_prompt import get_system_prompt
from .tools import (
    CodeFormattingMixin,
    CodeToolsMixin,
    ErrorFixingMixin,
    FileIOToolsMixin,
    ProjectManagementMixin,
    TestingMixin,
    TypeScriptToolsMixin,
    ValidationAndParsingMixin,
    WebToolsMixin,
)

# Import CLI tools
from .tools.cli_tools import CLIToolsMixin

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
    CLIToolsMixin,  # Universal CLI execution with process management
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
                - step_through: Enable step-through debugging mode (default: False)
                - max_plan_iterations: Maximum plan cycles before forcing completion (default: 2 for API mode, 3 otherwise)
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
        # Set more conservative max_plan_iterations for API mode to prevent infinite loops
        # API mode is detected by presence of output_handler
        if "max_plan_iterations" not in kwargs:
            # If output_handler is provided (API mode), use 2 iterations
            # Otherwise use 3 for interactive CLI mode where user can monitor
            if "output_handler" in kwargs and kwargs["output_handler"] is not None:
                kwargs["max_plan_iterations"] = 2
            else:
                kwargs["max_plan_iterations"] = 3

        # Step-through debugging mode
        self.step_through = kwargs.pop("step_through", False)

        # Ensure .gaia cache directory exists for temporary files
        self.cache_dir = Path.home() / ".gaia" / "cache"
        self.cache_dir.mkdir(parents=True, exist_ok=True)

        # Security: Configure allowed paths for file operations
        self.allowed_paths = kwargs.pop("allowed_paths", None)
        self.path_validator = PathValidator(self.allowed_paths)

        # Project planning state
        self.plan = None
        self.project_root = None

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
            logger.info(
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
        self.register_cli_tools()  # CLIToolsMixin (Universal CLI execution)

    def process_query(
        self, user_input: str, workspace_root=None, progress_callback=None, **kwargs
    ):  # pylint: disable=arguments-differ
        """Process a query with optional workspace root and progress callback.

        Args:
            user_input: The user's query
            workspace_root: Optional workspace directory for file operations (from VSCode)
            progress_callback: Optional callback function for progress updates
            **kwargs: Additional arguments passed to base process_query

        Returns:
            Result from processing the query
        """
        # Store workspace root and change to it if provided
        if workspace_root:
            self.workspace_root = workspace_root
            original_cwd = os.getcwd()
            os.chdir(workspace_root)
            logger.info(f"Changed working directory to: {workspace_root}")

        # Store progress callback for tools to use
        if progress_callback:
            self.progress_callback = progress_callback

        # Update system prompt based on actual user input for language detection
        # Reconstruct full prompt with language-specific base + tools
        base_prompt = self._get_system_prompt(user_input)
        self.system_prompt = (
            base_prompt + f"\n\n==== AVAILABLE TOOLS ====\n{self.tools_description}\n\n"
        )

        try:
            if self.step_through:
                result = self._process_query_with_stepping(user_input, **kwargs)
            else:
                result = super().process_query(user_input, **kwargs)

            return result
        finally:
            # Restore original working directory if we changed it
            if workspace_root:
                os.chdir(original_cwd)
                logger.info(f"Restored working directory to: {original_cwd}")

    def _process_query_with_stepping(self, user_input: str, **kwargs):
        """Process query with step-through debugging enabled.

        Args:
            user_input: The user's query
            **kwargs: Additional arguments

        Returns:
            Result from processing the query
        """
        import sys

        # pylint: disable=no-member
        self.console.print("\n" + "=" * 80)
        self.console.print("🐛 STEP-THROUGH DEBUG MODE ENABLED")
        self.console.print("=" * 80)
        self.console.print("\nCommands:")
        self.console.print("  [Enter]    - Continue to next step")
        self.console.print("  'c'        - Continue without stepping")
        self.console.print("  'q'        - Quit")
        self.console.print("  's'        - Show current state")
        self.console.print("  'v <var>'  - View variable value")
        self.console.print("=" * 80 + "\n")

        # Store original _process_turn method
        # pylint: disable=access-member-before-definition,attribute-defined-outside-init
        original_process_turn = self._process_turn
        step_mode = True

        def wrapped_process_turn(*args, **kwargs):
            nonlocal step_mode

            if step_mode:
                self.console.print("\n" + "-" * 80)
                self.console.print(f"📍 STEP {self.current_step}/{self.max_steps}")
                self.console.print(f"   State: {self.state}")
                self.console.print(
                    f"   Conversation messages: {len(self.conversation)}"
                )
                self.console.print("-" * 80)

                while True:
                    try:
                        cmd = input("🐛 Debug> ").strip().lower()

                        if cmd == "" or cmd == "n":
                            # Continue to next step
                            break
                        elif cmd == "c":
                            # Continue without stepping
                            step_mode = False
                            self.console.print("▶️  Continuing without stepping...")
                            break
                        elif cmd == "q":
                            # Quit
                            self.console.print("⏹️  Quitting...")
                            sys.exit(0)
                        elif cmd == "s":
                            # Show state
                            self.console.print("\n📊 Current State:")
                            self.console.print(
                                f"   Step: {self.current_step}/{self.max_steps}"
                            )
                            self.console.print(f"   State: {self.state}")
                            self.console.print(f"   Messages: {len(self.conversation)}")
                            if hasattr(self, "plan"):
                                self.console.print(f"   Plan: {self.plan is not None}")
                            if hasattr(self, "project_root"):
                                self.console.print(
                                    f"   Project root: {self.project_root}"
                                )
                        elif cmd.startswith("v "):
                            # View variable
                            var_name = cmd[2:].strip()
                            if hasattr(self, var_name):
                                value = getattr(self, var_name)
                                self.console.print(f"\n{var_name} = {value}")
                            else:
                                self.console.print(
                                    f"❌ Variable '{var_name}' not found"
                                )
                        else:
                            self.console.print(
                                "❓ Unknown command. Use [Enter], 'c', 'q', 's', or 'v <var>'"
                            )
                    except (EOFError, KeyboardInterrupt):
                        self.console.print("\n⏹️  Interrupted")
                        sys.exit(0)

            # Call original method
            return original_process_turn(*args, **kwargs)

        # Replace method temporarily
        self._process_turn = wrapped_process_turn

        try:
            # Process the query
            result = super().process_query(user_input, **kwargs)
        finally:
            # Restore original method
            self._process_turn = original_process_turn

        self.console.print("\n" + "=" * 80)
        self.console.print("🐛 STEP-THROUGH DEBUG SESSION ENDED")
        self.console.print("=" * 80 + "\n")

        return result


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
