# Copyright(C) 2024-2025 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
GAIA Jira App - Natural language interface for Jira.

This app provides:
- Natural language commands for Jira
- Bulk processing of tasks from meeting notes or specs
- Intelligent orchestration of multi-step workflows
"""

import asyncio
import sys
from typing import Dict, Any, List, Optional
from dataclasses import dataclass
import logging

from gaia.agents.jira.agent import JiraAgent

logger = logging.getLogger(__name__)


@dataclass
class TaskResult:
    """Result from a task execution."""

    success: bool
    action: str
    data: Any
    error: Optional[str] = None


class JiraApp:
    """
    Main application class for interacting with Jira.
    Uses the JiraOrchestrator for all operations.
    """

    def __init__(
        self,
        verbose: bool = False,
        debug: bool = False,
        model: str = None,
        step_mode: bool = False,
    ):
        """
        Initialize the Jira App.

        Args:
            verbose: Enable verbose output (deprecated, use debug)
            debug: Enable debug logging
            model: LLM model to use (optional)
            step_mode: Enable step-by-step execution mode
        """
        self.verbose = verbose
        self.debug = debug
        self.model = model or "Qwen3-Coder-30B-A3B-Instruct-GGUF"
        self.step_mode = step_mode
        # In demo/debug mode, never use silent mode so we see all agent steps
        self.agent = JiraAgent(
            model_id=self.model,
            debug_prompts=False,  # Don't include prompts in conversation history by default
            show_prompts=False,  # Don't show prompts by default, even in debug mode
            show_stats=self.debug or self.verbose,
            silent_mode=False,  # Always show agent steps for compelling demos
            debug=self.debug,
        )

        # Configure logging based on debug flag
        if self.debug:
            logging.basicConfig(
                level=logging.DEBUG,
                format="[%(asctime)s] | %(levelname)s | %(name)s | %(filename)s:%(lineno)d | %(message)s",
            )
            logger.setLevel(logging.DEBUG)
        else:
            logging.basicConfig(level=logging.WARNING)
            logger.setLevel(logging.WARNING)

    async def connect(self) -> bool:
        """Initialize the agent (compatibility method)."""
        # The agent initializes on first use
        return True

    async def disconnect(self):
        """Cleanup (compatibility method)."""
        # Nothing to cleanup
        ...

    async def execute_command(
        self, command: str, context: Optional[str] = None, output_to_file: bool = False
    ) -> TaskResult:
        """
        Execute a natural language command.

        Args:
            command: Natural language command to execute
            context: Additional context (optional)
            output_to_file: Whether to save output to file

        Returns:
            TaskResult with execution outcome
        """
        try:
            logger.debug(f"Executing command: {command}")
            if context:
                command = f"{command}\nContext: {context}"

            # Use the agent's process_query directly - single entry point
            result = self.agent.process_query(command, output_to_file=output_to_file)

            # Base agent returns: status ("success", "failed", "incomplete"), result, conversation, etc.
            return TaskResult(
                success=result.get("status") == "success",
                action="query",
                data=result,
                error=(
                    result.get("error_history", [""])[0]
                    if result.get("error_history")
                    else None
                ),
            )

        except Exception as e:
            logger.error(f"Command execution failed: {e}")
            return TaskResult(success=False, action="error", data={}, error=str(e))

    async def search(self, query: str) -> TaskResult:
        """
        Search for issues using natural language.

        Args:
            query: Natural language search query

        Returns:
            TaskResult with search results
        """
        return await self.execute_command(f"search {query}")

    async def create_issue(
        self,
        summary: str,
        description: Optional[str] = None,
        issue_type: str = "Task",
        priority: Optional[str] = None,
        project: Optional[str] = None,
    ) -> TaskResult:
        """
        Create a new Jira issue.

        Args:
            summary: Issue summary/title
            description: Issue description
            issue_type: Type of issue (Bug, Task, Story)
            priority: Issue priority
            project: Project key

        Returns:
            TaskResult with created issue details
        """
        command_parts = [f"create a {issue_type.lower()} titled '{summary}'"]

        if description:
            command_parts.append(f"with description '{description}'")
        if priority:
            command_parts.append(f"with {priority} priority")
        if project:
            command_parts.append(f"in project {project}")

        command = " ".join(command_parts)
        return await self.execute_command(command)

    async def bulk_create_from_notes(self, notes: str) -> List[TaskResult]:
        """
        Create multiple issues from meeting notes or specifications.

        Args:
            notes: Text containing tasks to create

        Returns:
            List of TaskResults for each created issue
        """
        # Parse notes to identify individual tasks
        lines = notes.strip().split("\n")
        results = []

        for line in lines:
            line = line.strip()
            if not line or line.startswith("#"):
                continue

            # Simple heuristic: lines starting with -, *, or numbers are tasks
            if any(line.startswith(prefix) for prefix in ["-", "*", "•"]) or (
                len(line) > 2 and line[0].isdigit() and line[1] in ".):"
            ):
                # Remove the prefix
                task_text = line.lstrip("-*•0123456789.): ").strip()
                if task_text:
                    result = await self.create_issue(
                        summary=task_text,
                        description=f"Created from bulk notes: {task_text}",
                    )
                    results.append(result)

        return results

    def _display_result(self, result: TaskResult):
        """Display result in a formatted way."""
        if result.success:
            print("✅ Success!")
            if result.data and isinstance(result.data, dict):
                # Extract and display tool execution results from conversation
                self._extract_and_display_tool_results(result.data)

                # Show debug information if enabled
                if self.debug:
                    print(f"🔍 Steps taken: {result.data.get('steps_taken', 0)}")
                    print(
                        f"💬 Conversation length: {len(result.data.get('conversation', []))}"
                    )

                    # Show JSON trace file if created
                    if result.data.get("output_file"):
                        print(f"📄 JSON trace saved to: {result.data['output_file']}")
                        print(
                            "   This file contains complete conversation history, tool calls, and performance stats"
                        )
        else:
            print(f"❌ Failed: {result.error}")

    def _extract_and_display_tool_results(self, result_data: Dict[str, Any]):
        """Extract tool execution results from conversation history and display them."""
        conversation = result_data.get("conversation", [])
        tool_results_found = False

        # Look through conversation for tool execution results
        for message in conversation:
            if message.get("role") == "system" and isinstance(
                message.get("content"), dict
            ):
                content = message["content"]

                # Check for Jira search results
                if "issues" in content:
                    tool_results_found = True
                    issues = content["issues"]
                    total = content.get("total", len(issues))
                    jql = content.get("jql", "")

                    print(f"🎫 Found {total} issues")
                    if jql:
                        print(f"📝 JQL: {jql}")

                    if issues:
                        print("\n📋 Issues:")
                        for issue in issues:
                            print(
                                f"  • {issue.get('key', 'N/A')} - {issue.get('summary', 'N/A')}"
                            )
                            status_info = []
                            if issue.get("status"):
                                status_info.append(f"Status: {issue['status']}")
                            if issue.get("priority"):
                                status_info.append(f"Priority: {issue['priority']}")
                            if issue.get("assignee"):
                                status_info.append(f"Assignee: {issue['assignee']}")
                            if status_info:
                                print(f"    {' | '.join(status_info)}")
                    else:
                        print("    No issues found matching the criteria")

                # Check for issue creation results
                elif content.get("status") == "success" and content.get("created"):
                    tool_results_found = True
                    print(f"✅ Created issue: {content.get('key', 'N/A')}")
                    if content.get("url"):
                        print(f"🔗 URL: {content['url']}")

                # Check for update results
                elif content.get("status") == "success" and content.get("updated"):
                    tool_results_found = True
                    print(f"✅ Updated issue: {content.get('key', 'N/A')}")
                    if content.get("url"):
                        print(f"🔗 URL: {content['url']}")

        # If no specific tool results found, show the generic result
        if not tool_results_found:
            final_result = result_data.get("result", "Task completed successfully")
            if final_result != "Task completed successfully":
                print(f"📋 {final_result}")
            else:
                print("📋 Task completed successfully")

    def _display_tool_data(self, tool_data: Dict[str, Any]):
        """Display tool execution data in a formatted way."""
        if "issues" in tool_data:
            issues = tool_data["issues"]
            total = tool_data.get("total", len(issues))
            print(f"    🎫 Found {total} issues")
            if self.debug and tool_data.get("jql"):
                print(f"    📝 JQL: {tool_data['jql']}")
            for issue in issues[:5]:  # Show first 5
                print(f"      • {issue['key']} - {issue['summary']}")
                if issue.get("status"):
                    print(
                        f"        Status: {issue['status']}, Priority: {issue.get('priority', 'N/A')}"
                    )

        elif tool_data.get("created"):
            print(f"    ✅ Created issue: {tool_data.get('key')}")
            print(f"    🔗 URL: {tool_data.get('url')}")

        elif tool_data.get("message"):
            print(f"    ℹ️ {tool_data['message']}")


async def main(cli_args=None):
    """Main entry point for the Jira app.

    Args:
        cli_args: Pre-parsed arguments from CLI, or None to parse from sys.argv
    """
    # pylint: disable=protected-access

    if cli_args is not None:
        # Use pre-parsed arguments from CLI
        args = cli_args
    else:
        # Parse arguments ourselves (for standalone usage)
        import argparse

        parser = argparse.ArgumentParser(description="GAIA Jira App")
        parser.add_argument("command", nargs="?", help="Command to execute")
        parser.add_argument(
            "-v",
            "--verbose",
            action="store_true",
            help="Enable verbose output (deprecated)",
        )
        parser.add_argument(
            "-d", "--debug", action="store_true", help="Enable debug logging"
        )
        parser.add_argument(
            "-i", "--interactive", action="store_true", help="Interactive mode"
        )
        parser.add_argument("--model", help="LLM model to use")

        args = parser.parse_args()

    # Create app
    app = JiraApp(verbose=args.verbose, debug=args.debug, model=args.model)
    try:
        # Initialize
        if not await app.connect():
            print("❌ Failed to initialize Jira app")
            return 1

        if args.interactive:
            # Interactive mode
            print("🚀 GAIA Jira App - Interactive Mode")
            print("Type 'help' for commands, 'exit' to quit\n")

            while True:
                try:
                    command = input("jira> ").strip()

                    if command.lower() in ["exit", "quit", "q"]:
                        break
                    elif command.lower() in ["help", "h", "?"]:
                        print(
                            """
Commands:
  search <query>       - Search for issues
  create <description> - Create a new issue
  help                 - Show this help
  exit                 - Exit the app

Examples:
  search critical bugs
  create Fix login timeout issue
  search my open issues
                        """
                        )
                    elif command:
                        result = await app.execute_command(command)
                        app._display_result(result)  # pylint: disable=protected-access

                except KeyboardInterrupt:
                    print("\n")
                    break
                except Exception as e:
                    print(f"❌ Error: {e}")

        elif args.command:
            # Single command mode
            result = await app.execute_command(args.command)
            app._display_result(result)  # pylint: disable=protected-access
        else:
            parser.print_help()

    finally:
        await app.disconnect()

    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
