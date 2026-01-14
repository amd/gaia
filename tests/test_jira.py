#!/usr/bin/env python
# Copyright(C) 2024-2025 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Integration Test Suite for GAIA Jira Agent.

This test suite validates all Jira agent functionality with REAL API calls:
- Natural language query processing
- JQL generation and execution
- Issue creation, search, and updates
- Multi-criteria queries
- Error handling and recovery
- Base agent framework integration

NO MOCKS - All tests use real Jira API calls.
Requires ATLASSIAN_SITE_URL, ATLASSIAN_API_KEY, ATLASSIAN_USER_EMAIL environment variables.

Usage:
    # Run all tests
    python tests/test_jira.py

    # Run specific test
    python tests/test_jira.py --test test_basic_fetch_queries

    # Enable debug mode (debug logging, does NOT include prompt display)
    python tests/test_jira.py --debug

    # Show prompts sent to LLM (separate from debug mode)
    python tests/test_jira.py --show-prompts

    # Both debug and prompts
    python tests/test_jira.py --debug --show-prompts

    # Interactive mode - select tests from menu
    python tests/test_jira.py --interactive

    # Step mode - pause after each test
    python tests/test_jira.py --step

    # Export results to CSV
    python tests/test_jira.py --csv results.csv

    # Use Claude API instead of local LLM
    python tests/test_jira.py --use-claude

    # Use ChatGPT/OpenAI API
    python tests/test_jira.py --use-chatgpt

    # Use local LLM (default behavior - no flag needed)
    python tests/test_jira.py

    # Combine flags
    python tests/test_jira.py --show-prompts --step --csv --use-claude
"""

import asyncio
import csv
import datetime
import os
import sys
from pathlib import Path
from typing import Dict, List

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from gaia.agents.jira.agent import JiraAgent
from gaia.apps.jira.app import JiraApp, TaskResult


class JiraIntegrationTests:
    """Real integration tests against live Jira instance."""

    def __init__(
        self,
        step_mode=False,
        csv_output=None,
        model=None,
        json_output=True,
        debug=False,
        show_prompts=False,
        use_claude=False,
        use_chatgpt=False,
    ):
        self.app = None
        self.agent = None
        self.test_results = []
        self.step_mode = step_mode
        self.csv_output = csv_output
        self.model = model
        self.json_output = json_output
        self.debug = debug
        self.show_prompts = show_prompts
        self.use_claude = use_claude
        self.use_chatgpt = use_chatgpt
        self.test_start_time = None
        self.test_end_time = None
        self.csv_file_initialized = False
        self.tests_completed = 0
        self.tests_passed = 0
        self.tests_failed = 0

    def check_credentials(self) -> bool:
        """Check if Jira credentials are available."""
        required_env = [
            "ATLASSIAN_SITE_URL",
            "ATLASSIAN_API_KEY",
            "ATLASSIAN_USER_EMAIL",
        ]
        missing = [key for key in required_env if not os.getenv(key)]

        if missing:
            print(f"❌ Missing required environment variables: {', '.join(missing)}")
            print("\nPlease set these variables:")
            for key in missing:
                if key == "ATLASSIAN_SITE_URL":
                    print(f"  export {key}=https://yoursite.atlassian.net")
                elif key == "ATLASSIAN_API_KEY":
                    print(f"  export {key}=your_api_token")
                elif key == "ATLASSIAN_USER_EMAIL":
                    print(f"  export {key}=your_email@domain.com")
            return False

        return True

    async def setup(self) -> bool:
        """Initialize test environment."""
        if not self.check_credentials():
            return False

        print("🔧 Setting up test environment...")
        if self.model:
            print(f"🤖 Using model: {self.model}")
        if self.debug:
            print("🐛 Debug mode enabled")
        if self.show_prompts:
            print("📝 Prompt display enabled")

        # Initialize CSV file if output is requested
        if self.csv_output:
            self._initialize_csv()

        # Use custom model if provided
        self.app = JiraApp(debug=self.debug, model=self.model)
        agent_kwargs = {
            "use_claude": self.use_claude,
            "use_chatgpt": self.use_chatgpt,
            "debug_prompts": False,  # Include prompts in conversation history
            "show_prompts": self.show_prompts,  # Only show prompts if explicitly enabled with --show-prompts
            "show_stats": False,
            "debug": self.debug,  # Pass debug flag to agent
        }
        # Only override model if explicitly specified (for local LLM)
        if self.model is not None and not (self.use_claude or self.use_chatgpt):
            agent_kwargs["model_id"] = self.model
        self.agent = JiraAgent(**agent_kwargs)

        # Initialize agent with discovered Jira configuration
        print("🔧 Discovering Jira configuration...")
        self.jira_config = self.agent.initialize()

        # Extract useful values for dynamic tests
        self.project_keys = [p["key"] for p in self.jira_config.get("projects", [])]
        self.issue_types = self.jira_config.get("issue_types", [])
        self.statuses = self.jira_config.get("statuses", [])
        self.priorities = self.jira_config.get("priorities", [])

        print(
            f"📋 Discovered: {len(self.project_keys)} projects, {len(self.issue_types)} issue types, {len(self.statuses)} statuses"
        )

        if not await self.app.connect():
            print("❌ Failed to connect to Jira")
            return False

        print("✅ Test environment ready!")
        return True

    async def teardown(self):
        """Cleanup test environment."""
        if self.app:
            await self.app.disconnect()
        print("🧹 Test environment cleaned up")

    def record_result(
        self,
        test_name: str,
        success: bool,
        details: str = "",
        duration: float = 0.0,
        category: str = "",
        query: str = "",
        steps_taken: int = 0,
        error_count: int = 0,
        input_tokens: int = 0,
        output_tokens: int = 0,
        test_method: str = "",
    ):
        """Record test result with enhanced metadata."""
        result = {
            "name": test_name,
            "success": success,
            "details": details,
            "duration": duration,
            "category": category,
            "query": query,
            "steps_taken": steps_taken,
            "error_count": error_count,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "total_tokens": input_tokens + output_tokens,
            "timestamp": datetime.datetime.now().isoformat(),
            "test_method": test_method,
        }
        self.test_results.append(result)

        # Write to CSV immediately after recording each result
        if self.csv_output:
            self._append_to_csv(result)

    def _initialize_csv(self):
        """Initialize CSV file with headers."""
        if not self.csv_output or self.csv_file_initialized:
            return

        # Generate filename if True was passed
        if self.csv_output is True:
            timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            self.csv_output = f"test_results_{timestamp}.csv"

        try:
            with open(self.csv_output, "w", newline="", encoding="utf-8") as csvfile:
                # Write header information
                csvfile.write("=" * 80 + "\n")
                csvfile.write("GAIA JIRA AGENT TEST REPORT\n")
                csvfile.write("=" * 80 + "\n")
                csvfile.write(
                    f"Test Run Started: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
                )
                csvfile.write("\n")

                # Write configuration section
                csvfile.write("TEST CONFIGURATION\n")
                csvfile.write("-" * 40 + "\n")

                # Get configuration details
                config_details = self._get_configuration_details()
                for key, value in config_details.items():
                    csvfile.write(f"{key}: {value}\n")

                csvfile.write("\n")
                csvfile.write("TEST EXECUTION DETAILS\n")
                csvfile.write("-" * 40 + "\n")

                # Write column headers
                fieldnames = [
                    "Category",
                    "Test Name",
                    "Test Method",
                    "Query/Command",
                    "Status",
                    "Running Total (P/F/T)",
                    "Success Rate",
                    "Steps",
                    "Errors",
                    "Input Tokens",
                    "Output Tokens",
                    "Total Tokens",
                    "Duration (s)",
                    "Timestamp",
                    "Failure Reason",
                ]
                writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
                writer.writeheader()

            self.csv_file_initialized = True
            print(f"📊 Real-time CSV logging to: {self.csv_output}")

        except Exception as e:
            print(f"❌ Failed to initialize CSV file: {e}")
            self.csv_output = None

    def _append_to_csv(self, result: dict):
        """Append a single test result to CSV and show running summary."""
        if not self.csv_output or not self.csv_file_initialized:
            return

        # Update running totals
        self.tests_completed += 1
        if result["success"]:
            self.tests_passed += 1
        else:
            self.tests_failed += 1

        success_rate = (
            (self.tests_passed / self.tests_completed * 100)
            if self.tests_completed > 0
            else 0
        )

        try:
            with open(self.csv_output, "a", newline="", encoding="utf-8") as csvfile:
                fieldnames = [
                    "Category",
                    "Test Name",
                    "Test Method",
                    "Query/Command",
                    "Status",
                    "Running Total (P/F/T)",
                    "Success Rate",
                    "Steps",
                    "Errors",
                    "Input Tokens",
                    "Output Tokens",
                    "Total Tokens",
                    "Duration (s)",
                    "Timestamp",
                    "Failure Reason",
                ]
                writer = csv.DictWriter(csvfile, fieldnames=fieldnames)

                # Truncate query if too long for CSV readability
                query_text = result.get("query", "")
                if len(query_text) > 100:
                    query_text = query_text[:97] + "..."

                writer.writerow(
                    {
                        "Category": result.get("category", "Uncategorized"),
                        "Test Name": result["name"],
                        "Test Method": result.get("test_method", ""),
                        "Query/Command": query_text,
                        "Status": "PASSED" if result["success"] else "FAILED",
                        "Running Total (P/F/T)": f"{self.tests_passed}/{self.tests_failed}/{self.tests_completed}",
                        "Success Rate": f"{success_rate:.1f}%",
                        "Steps": result.get("steps_taken", 0),
                        "Errors": result.get("error_count", 0),
                        "Input Tokens": result.get("input_tokens", 0),
                        "Output Tokens": result.get("output_tokens", 0),
                        "Total Tokens": result.get("total_tokens", 0),
                        "Duration (s)": f"{result.get('duration', 0):.2f}",
                        "Timestamp": result.get("timestamp", ""),
                        "Failure Reason": (
                            result["details"].replace("\n", " ").replace("\r", " ")
                            if not result["success"] and result["details"]
                            else ""
                        ),
                    }
                )

            # Print running summary after each test
            status_icon = "✅" if result["success"] else "❌"
            print(
                f"\n📊 Test #{self.tests_completed} Complete: {status_icon} {result['name']}"
            )
            print(
                f"   Duration: {result.get('duration', 0):.2f}s | Steps: {result.get('steps_taken', 0)} | Errors: {result.get('error_count', 0)}"
            )
            print(
                f"   Tokens: Input={result.get('input_tokens', 0)} | Output={result.get('output_tokens', 0)} | Total={result.get('total_tokens', 0)}"
            )
            print(
                f"   Running Total: {self.tests_passed} passed, {self.tests_failed} failed (Success Rate: {success_rate:.1f}%)"
            )
            print(f"   CSV updated: {self.csv_output}")

        except Exception as e:
            print(f"❌ Failed to append to CSV: {e}")

    def wait_for_user(self, test_name: str):
        """Wait for user input before proceeding to next test in step mode."""
        if self.step_mode:
            print(f"\n{'='*60}")
            print(f"⏸️  Test '{test_name}' completed.")
            print("📋 Check the agent_output_*.json files in the current directory")
            print("Press Enter to continue to the next test, or 'q' to quit...")
            user_input = input().strip().lower()
            if user_input == "q":
                print("🛑 Stopping tests at user request")
                return False
        return True

    def _get_test_category(self, test_name: str) -> str:
        """Get the category for a test based on its name."""
        catalog = self.get_test_catalog()
        for category, tests in catalog:
            for name, _ in tests:
                if name == test_name:
                    return category
        return "Uncategorized"

    def _get_configuration_details(self) -> Dict[str, str]:
        """Get configuration details for the test run."""
        import platform

        # Determine LLM configuration
        if self.use_claude:
            llm_info = "Claude API (claude-sonnet-4-20250514)"
        elif self.use_chatgpt:
            llm_info = "OpenAI API (gpt-4o)"
        else:
            llm_info = self.model or "Default (Qwen3-Coder-30B-A3B-Instruct-GGUF)"

        config = {
            "Test Environment": f"{platform.system()} {platform.release()}",
            "Python Version": platform.python_version(),
            "LLM Model": llm_info,
            "Agent Type": "JiraAgent with automatic configuration discovery",
            "Debug Mode": "Enabled" if self.debug else "Disabled",
            "Prompt Display": "Enabled" if self.show_prompts else "Disabled",
            "JSON Output": "Enabled" if self.json_output else "Disabled",
            "Step Mode": "Enabled" if self.step_mode else "Disabled",
            "Jira Instance": os.getenv("ATLASSIAN_SITE_URL", "Not configured"),
        }

        # Add discovered Jira configuration if available
        if hasattr(self, "jira_config") and self.jira_config:
            config["Projects Discovered"] = (
                str(len(self.project_keys)) if self.project_keys else "0"
            )
            config["Issue Types Available"] = (
                str(len(self.issue_types)) if self.issue_types else "0"
            )
            config["Statuses Available"] = (
                str(len(self.statuses)) if self.statuses else "0"
            )
            config["Priorities Available"] = (
                str(len(self.priorities)) if self.priorities else "0"
            )

        return config

    def _extract_metadata(self, result) -> tuple:
        """Extract metadata from result object."""
        if hasattr(result, "data") and isinstance(result.data, dict):
            duration = result.data.get("_test_duration", 0) or result.data.get(
                "duration", 0
            )
            steps_taken = result.data.get("steps_taken", 0)
            error_count = result.data.get("error_count", 0)
            input_tokens = result.data.get("input_tokens", 0)
            output_tokens = result.data.get("output_tokens", 0)
        else:
            duration = 0
            steps_taken = 0
            error_count = 0
            input_tokens = 0
            output_tokens = 0
        return duration, steps_taken, error_count, input_tokens, output_tokens

    async def _run_query_test(
        self, query: str, test_name: str, test_num: int = 0, total_tests: int = 0
    ) -> bool:
        """Run a single query test with consistent error handling and reporting.

        Args:
            query: The query to test
            test_name: Name of the test for reporting
            test_num: Current test number (for batch tests)
            total_tests: Total number of tests (for batch tests)

        Returns:
            True if test passed, False otherwise
        """
        import time

        query_start = time.time()

        # Determine test category
        import inspect

        calling_method = inspect.stack()[1].function
        category = self._get_test_category(calling_method)

        # Format test name for batch tests
        if test_num > 0 and total_tests > 0:
            display_name = f"{test_name} {test_num}/{total_tests}"
        else:
            display_name = test_name

        try:
            # Execute query
            if self.json_output:
                result = self._execute_agent_query(query, calling_method)
                duration, steps_taken, error_count, input_tokens, output_tokens = (
                    self._extract_metadata(result)
                )
            else:
                result = await self.app.execute_command(query)
                duration = time.time() - query_start
                steps_taken = 0
                error_count = 0
                input_tokens = 0
                output_tokens = 0

            # Check for success
            if not result.success:
                # Extract the actual error message from result
                error_msg = (
                    result.error
                    if hasattr(result, "error") and result.error
                    else "Query failed"
                )
                # Clean up the error message - remove newlines
                error_msg = str(error_msg).replace("\n", " ").replace("\r", " ").strip()

                # Record failure with actual error message
                if duration == 0:
                    duration = time.time() - query_start

                self.record_result(
                    display_name,
                    False,
                    error_msg,
                    duration=duration,
                    category=category,
                    test_method=calling_method,
                    query=query,
                    steps_taken=steps_taken,
                    error_count=error_count,
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                )

                query_preview = str(query)[:50] if query else "unknown query"
                if test_num > 0:
                    print(
                        f"   ❌ Query {test_num}/{total_tests}: {query_preview}... - FAILED: {error_msg}"
                    )
                else:
                    print(f"   ❌ FAILED - {error_msg}")

                return False

            # Verify action type
            if hasattr(result, "action"):
                assert (
                    result.action == "query"
                ), f"Expected action=query for '{query}', got {result.action}"

            # Record success
            if duration == 0:
                duration = time.time() - query_start

            self.record_result(
                display_name,
                True,
                duration=duration,
                category=category,
                test_method=calling_method,
                query=query,
                steps_taken=steps_taken,
                error_count=error_count,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
            )

            query_preview = str(query)[:50] if query else "unknown query"
            if test_num > 0:
                print(
                    f"   ✅ Query {test_num}/{total_tests}: {query_preview}... - PASSED"
                )
            else:
                print(f"   ✅ PASSED - {display_name}")

            return True

        except Exception as e:
            # Handle unexpected exceptions
            duration = time.time() - query_start

            # Try to extract metadata from partial result if available
            if "result" in locals():
                try:
                    _, steps_taken, error_count, input_tokens, output_tokens = (
                        self._extract_metadata(result)
                    )
                except Exception as meta_error:
                    # Log the metadata extraction error for debugging
                    print(f"      Warning: Failed to extract metadata: {meta_error}")
                    steps_taken = error_count = input_tokens = output_tokens = 0
            else:
                steps_taken = error_count = input_tokens = output_tokens = 0

            # Clean up error message
            error_msg = str(e).replace("\n", " ").replace("\r", " ").strip()

            self.record_result(
                display_name,
                False,
                error_msg,
                duration=duration,
                category=category,
                test_method=calling_method,
                query=query,
                steps_taken=steps_taken,
                error_count=error_count,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
            )

            query_preview = str(query)[:50] if query else "unknown query"
            if test_num > 0:
                print(
                    f"   ❌ Query {test_num}/{total_tests}: {query_preview}... - FAILED: {error_msg}"
                )
            else:
                print(f"   ❌ FAILED - {error_msg}")

            return False

    def _execute_agent_query(self, query: str, test_name: str) -> TaskResult:
        """Execute a query using the agent and evaluate success properly."""
        if self.json_output:
            # Call agent's process_query directly to ensure JSON file is created
            result_dict = self.agent.process_query(query, output_to_file=True)

            # Check if JSON file was created
            import glob

            json_files = glob.glob("agent_output_*.json")
            if json_files:
                latest_file = max(json_files, key=os.path.getctime)
                print(f"   📄 JSON output saved to: {latest_file}")

            # Check for success based on error count and final answer
            error_count = result_dict.get("error_count", 0)
            has_final_answer = result_dict.get("result") is not None
            has_errors = error_count > 0

            # Log the evaluation criteria
            if has_errors:
                print(f"   ⚠️  Agent had {error_count} errors during execution")
                # Show error details for debugging
                error_history = result_dict.get("error_history", [])
                for i, error in enumerate(error_history[:2], 1):  # Show first 2 errors
                    # Handle error as dictionary or string
                    if isinstance(error, dict):
                        error_str = error.get("error", str(error))
                    else:
                        error_str = str(error)
                    # Truncate for display
                    if len(error_str) > 100:
                        error_str = error_str[:100] + "..."
                    print(f"      Error {i}: {error_str}")

            # Log duration if available
            duration = result_dict.get("duration", 0)
            if duration > 0:
                print(f"   ⏱️  Query took {duration:.2f} seconds")

            # Success if we have a final answer AND no errors
            success = has_final_answer and not has_errors

            # Include duration in the result
            result_dict["_test_duration"] = duration

            # Build detailed error message with error history
            error_msg = None
            if has_errors:
                error_history = result_dict.get("error_history", [])
                if error_history:
                    # Include first few errors in the message
                    error_details = []
                    for i, err in enumerate(
                        error_history[:3], 1
                    ):  # Include up to 3 errors
                        # Handle error as dictionary or string
                        if isinstance(err, dict):
                            # Extract error message from dictionary - it's in the 'error' field
                            err_str = err.get("error", str(err))
                        else:
                            err_str = str(err)
                        # Clean up the error message - remove newlines and extra whitespace
                        err_str = err_str.replace("\n", " ").replace("\r", " ").strip()
                        # Truncate error to 150 chars for readability
                        if len(err_str) > 150:
                            err_str = err_str[:150] + "..."
                        error_details.append(f"Error {i}: {err_str}")
                    error_msg = f"Agent had {error_count} errors. Details: {'; '.join(error_details)}"
                else:
                    error_msg = f"Agent execution had {error_count} errors"

            return TaskResult(
                success=success, action="query", data=result_dict, error=error_msg
            )
        else:
            # Use the app's method (doesn't generate JSON files)
            import asyncio

            return asyncio.create_task(self.app.execute_command(query))

    def get_test_catalog(self):
        """Get organized catalog of all available tests."""
        return [
            # Core functionality tests
            (
                "Core Functionality",
                [
                    (
                        "test_agent_initialization",
                        "Test Jira agent initializes correctly",
                    ),
                    (
                        "test_search_my_issues",
                        "Test searching for user's assigned issues",
                    ),
                    ("test_time_based_query", "Test time-based JQL query generation"),
                    ("test_issue_type_query", "Test issue type filtering"),
                    (
                        "test_complex_multi_criteria_query",
                        "Test complex query with multiple criteria",
                    ),
                    (
                        "test_issue_creation",
                        "Test issue creation with correct parameters",
                    ),
                    (
                        "test_completion_no_timeout",
                        "Test that queries complete without timeout errors",
                    ),
                    ("test_app_methods", "Test app-level methods"),
                ],
            ),
            # Basic tests
            (
                "Basic Operations",
                [
                    ("test_basic_fetch_queries", "Test basic fetch queries"),
                    (
                        "test_advanced_creation_patterns",
                        "Test advanced issue creation patterns",
                    ),
                ],
            ),
            # Intermediate tests
            (
                "Intermediate Queries",
                [
                    (
                        "test_intermediate_search_queries",
                        "Test intermediate search queries",
                    ),
                    (
                        "test_status_priority_queries",
                        "Test status and priority-based queries",
                    ),
                    ("test_advanced_jql_queries", "Test advanced JQL query generation"),
                ],
            ),
            # Advanced tests
            (
                "Advanced Operations",
                [
                    ("test_time_based_analysis", "Test time-based analysis queries"),
                    ("test_complex_filters", "Test complex filtering queries"),
                    (
                        "test_multi_step_workflows",
                        "Test complex multi-step workflow queries",
                    ),
                ],
            ),
            # Expert tests
            (
                "Expert Features",
                [
                    (
                        "test_cross_project_analysis",
                        "Test cross-project analysis queries",
                    ),
                    (
                        "test_analytics_queries",
                        "Test analytics and summarization queries",
                    ),
                    ("test_bulk_operations", "Test bulk processing capabilities"),
                ],
            ),
            # Master tests
            (
                "Master Level",
                [
                    (
                        "test_complex_business_logic",
                        "Test complex business logic queries",
                    ),
                    (
                        "test_advanced_automation_scenarios",
                        "Test advanced automation scenarios",
                    ),
                    (
                        "test_predictive_analysis_queries",
                        "Test predictive analysis queries",
                    ),
                ],
            ),
            # Comprehensive tests
            (
                "Comprehensive Coverage",
                [
                    (
                        "test_missing_advanced_queries",
                        "Test advanced queries missing from other tests",
                    ),
                    (
                        "test_strategic_insights_queries",
                        "Test strategic insights and predictive queries",
                    ),
                ],
            ),
        ]

    def display_test_menu(self):
        """Display interactive test selection menu in two columns."""
        catalog = self.get_test_catalog()
        test_map = {}
        test_num = 1

        print("\n" + "=" * 140)
        print("📋 AVAILABLE TESTS")
        print("=" * 140)

        for category, tests in catalog:
            print(f"\n🔹 {category}:")

            # Display tests in two columns
            for i in range(0, len(tests), 2):
                left_test = tests[i]
                right_test = tests[i + 1] if i + 1 < len(tests) else None

                # Left column
                left_line = f"  [{test_num:2d}] {left_test[1][:45]:<45}"
                test_map[str(test_num)] = left_test[0]
                test_num += 1

                # Right column (if exists)
                if right_test:
                    right_line = f"  [{test_num:2d}] {right_test[1]}"
                    test_map[str(test_num)] = right_test[0]
                    test_num += 1
                    print(f"{left_line} | {right_line}")
                else:
                    print(left_line)

        print(f"\n  [ 0] Run ALL tests")
        print(f"  [ q] Quit")
        print("=" * 140)

        return test_map

    def _get_test_description(self, test_name: str) -> str:
        """Get human-readable description for a test method name."""
        catalog = self.get_test_catalog()
        for category, tests in catalog:
            for method_name, description in tests:
                if method_name == test_name:
                    return description
        return test_name  # Fallback to method name

    async def run_selected_tests(self, test_names: List[str]) -> bool:
        """Run a specific set of tests."""
        print(f"\n🚀 Running {len(test_names)} selected test(s)")
        print("=" * 60)

        passed = 0
        failed = 0

        for test_name in test_names:
            test_method = getattr(self, test_name, None)
            if test_method and callable(test_method):
                try:
                    description = self._get_test_description(test_name)
                    print(f"\n🎯 Running: {description}")
                    print(f"   Method: {test_name}")
                    success = await test_method()
                    if success:
                        passed += 1
                    else:
                        failed += 1

                    # Wait for user input in step mode
                    if not self.wait_for_user(test_name):
                        print("\n⚠️  Tests stopped by user")
                        break

                except Exception as e:
                    print(f"   ❌ EXCEPTION in {test_name}: {e}")
                    failed += 1

                    # Also wait after exceptions in step mode
                    if not self.wait_for_user(test_name):
                        print("\n⚠️  Tests stopped by user")
                        break
            else:
                print(f"❌ Test '{test_name}' not found!")
                failed += 1

        # Print summary
        print(f"\n{'='*60}")
        print("📊 TEST SUMMARY")
        print(f"{'='*60}")
        print(f"✅ Passed: {passed}")
        print(f"❌ Failed: {failed}")
        if (passed + failed) > 0:
            print(f"📈 Success Rate: {(passed / (passed + failed) * 100):.1f}%")

        return failed == 0

    async def test_agent_initialization(self) -> bool:
        """Test Jira agent initializes correctly."""
        print("\n🧪 Test: Agent Initialization")
        import time

        start_time = time.time()

        try:
            # Test agent properties
            assert self.agent is not None, "Agent should not be None"
            assert (
                self.agent.max_steps == 10
            ), f"Expected max_steps=10, got {self.agent.max_steps}"
            assert hasattr(
                self.agent, "_jira_credentials"
            ), "Agent should have _jira_credentials attribute"

            # Test system prompt contains necessary elements
            prompt = self.agent._get_system_prompt()

            # Check for dynamic configuration in prompt
            if self.jira_config and self.jira_config.get("issue_types"):
                # If configuration was discovered, check for actual issue types
                for issue_type in self.issue_types[:1]:  # Check at least one issue type
                    if (
                        f'issuetype = "{issue_type}"' in prompt
                        or f"Available issue types are" in prompt
                    ):
                        break
                else:
                    assert (
                        False
                    ), f"System prompt should reference discovered issue types: {self.issue_types}"
            else:
                # If no config, just check for generic issue type reference
                assert (
                    "issuetype" in prompt.lower() or "issue type" in prompt.lower()
                ), "System prompt should reference issue types"

            # Check for essential JQL syntax elements
            assert (
                "assignee = currentUser()" in prompt
            ), "System prompt should have user assignment syntax"

            # Check for status reference (either discovered or default)
            if self.jira_config and self.jira_config.get("statuses"):
                assert (
                    "Available statuses are" in prompt or "status" in prompt.lower()
                ), "System prompt should reference statuses"
            else:
                assert (
                    "status" in prompt.lower()
                ), "System prompt should reference status concept"

            # Test tool registration by checking the _TOOL_REGISTRY directly
            from gaia.agents.base.tools import _TOOL_REGISTRY

            tool_names = list(_TOOL_REGISTRY.keys())
            assert "jira_search" in tool_names, "jira_search tool should be registered"
            assert "jira_create" in tool_names, "jira_create tool should be registered"
            assert "jira_update" in tool_names, "jira_update tool should be registered"

            print("   ✅ PASSED - Agent initialized correctly")
            duration = time.time() - start_time
            self.record_result(
                "Agent Initialization",
                True,
                duration=duration,
                category="Core Functionality",
                test_method="test_agent_initialization",
            )
            return True

        except Exception as e:
            print(f"   ❌ FAILED - {e}")
            duration = time.time() - start_time
            self.record_result(
                "Agent Initialization",
                False,
                str(e),
                duration=duration,
                category="Core Functionality",
                test_method="test_agent_initialization",
            )
            return False

    async def test_search_my_issues(self) -> bool:
        """Test searching for user's assigned issues."""
        print("\n🧪 Test: Search My Issues")
        import time

        start_time = time.time()
        query = "what are my issues"

        try:
            # Execute the query and evaluate success properly
            if self.json_output:
                result = self._execute_agent_query(query, "test_search_my_issues")
                duration, steps_taken, error_count, input_tokens, output_tokens = (
                    self._extract_metadata(result)
                )
            else:
                result = await self.app.execute_command(query)
                duration = time.time() - start_time
                steps_taken = 0
                error_count = 0
                input_tokens = 0
                output_tokens = 0

            assert result.success, (
                result.error if hasattr(result, "error") else "Failed"
            )
            assert (
                result.action == "query"
            ), f"Expected action=query, got {result.action}"
            assert isinstance(result.data, dict), "Result data should be a dictionary"

            print("   ✅ PASSED - Successfully searched for assigned issues")
            # Use fallback duration if not available from agent
            if duration == 0:
                duration = time.time() - start_time
            self.record_result(
                "Search My Issues",
                True,
                duration=duration,
                category=self._get_test_category("test_search_my_issues"),
                test_method="test_search_my_issues",
                query=query,
                steps_taken=steps_taken,
                error_count=error_count,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
            )
            return True

        except Exception as e:
            print(f"   ❌ FAILED - {e}")
            duration = time.time() - start_time
            # Initialize metadata with default values on failure
            steps_taken = 0
            error_count = 0
            input_tokens = 0
            output_tokens = 0

            if "result" in locals():
                try:
                    _, steps_taken, error_count, input_tokens, output_tokens = (
                        self._extract_metadata(result)
                    )
                except Exception as meta_e:
                    # Log metadata extraction error
                    print(f"      Warning: Failed to extract metadata: {meta_e}")

            self.record_result(
                "Search My Issues",
                False,
                str(e),
                duration=duration,
                category=self._get_test_category("test_search_my_issues"),
                test_method="test_search_my_issues",
                query=query,
                steps_taken=steps_taken,
                error_count=error_count,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
            )
            return False

    async def test_time_based_query(self) -> bool:
        """Test time-based JQL query generation."""
        print("\n🧪 Test: Time-Based Query")
        import time

        start_time = time.time()
        query = "show me issues created this week"

        try:
            # Execute the query and evaluate success properly
            if self.json_output:
                result = self._execute_agent_query(query, "test_time_based_query")
                duration, steps_taken, error_count, input_tokens, output_tokens = (
                    self._extract_metadata(result)
                )
            else:
                result = await self.app.execute_command(query)
                duration = time.time() - start_time
                steps_taken = 0
                error_count = 0

            assert result.success, result.error
            assert (
                result.action == "query"
            ), f"Expected action=query, got {result.action}"

            print("   ✅ PASSED - Time-based query executed successfully")
            if duration == 0:
                duration = time.time() - start_time
            self.record_result(
                "Time-Based Query",
                True,
                duration=duration,
                category=self._get_test_category("test_time_based_query"),
                test_method="test_time_based_query",
                query=query,
                steps_taken=steps_taken,
                error_count=error_count,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
            )
            return True

        except Exception as e:
            print(f"   ❌ FAILED - {e}")
            duration = time.time() - start_time
            steps_taken = 0
            error_count = 0
            input_tokens = 0
            output_tokens = 0
            if "result" in locals():
                _, steps_taken, error_count, input_tokens, output_tokens = (
                    self._extract_metadata(result)
                )
            self.record_result(
                "Time-Based Query",
                False,
                str(e),
                duration=duration,
                category=self._get_test_category("test_time_based_query"),
                query=query,
                steps_taken=steps_taken,
                error_count=error_count,
                input_tokens=input_tokens if "input_tokens" in locals() else 0,
                output_tokens=output_tokens if "output_tokens" in locals() else 0,
            )
            return False

    async def test_issue_type_query(self) -> bool:
        """Test issue type filtering."""
        print("\n🧪 Test: Issue Type Query")
        import time

        start_time = time.time()
        query = "find all ideas"

        try:
            # Execute the query and evaluate success properly
            if self.json_output:
                result = self._execute_agent_query(query, "test_issue_type_query")
                duration, steps_taken, error_count, input_tokens, output_tokens = (
                    self._extract_metadata(result)
                )
            else:
                result = await self.app.execute_command(query)
                duration = time.time() - start_time
                steps_taken = 0
                error_count = 0

            assert result.success, result.error
            assert (
                result.action == "query"
            ), f"Expected action=query, got {result.action}"

            print("   ✅ PASSED - Issue type filtering works")
            if duration == 0:
                duration = time.time() - start_time
            self.record_result(
                "Issue Type Query",
                True,
                duration=duration,
                category=self._get_test_category("test_issue_type_query"),
                test_method="test_issue_type_query",
                query=query,
                steps_taken=steps_taken,
                error_count=error_count,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
            )
            return True

        except Exception as e:
            print(f"   ❌ FAILED - {e}")
            duration = time.time() - start_time
            steps_taken = 0
            error_count = 0
            input_tokens = 0
            output_tokens = 0
            if "result" in locals():
                _, steps_taken, error_count, input_tokens, output_tokens = (
                    self._extract_metadata(result)
                )
            self.record_result(
                "Issue Type Query",
                False,
                str(e),
                duration=duration,
                category=self._get_test_category("test_issue_type_query"),
                query=query,
                steps_taken=steps_taken,
                error_count=error_count,
                input_tokens=input_tokens if "input_tokens" in locals() else 0,
                output_tokens=output_tokens if "output_tokens" in locals() else 0,
            )
            return False

    async def test_complex_multi_criteria_query(self) -> bool:
        """Test complex query with multiple criteria."""
        print("\n🧪 Test: Multi-Criteria Query")
        import time

        start_time = time.time()
        query = "show me ideas assigned to me"

        try:
            # Execute the query and evaluate success properly
            if self.json_output:
                result = self._execute_agent_query(
                    query, "test_complex_multi_criteria_query"
                )
                duration, steps_taken, error_count, input_tokens, output_tokens = (
                    self._extract_metadata(result)
                )
            else:
                result = await self.app.execute_command(query)
                duration = time.time() - start_time
                steps_taken = 0
                error_count = 0

            assert result.success, result.error
            assert (
                result.action == "query"
            ), f"Expected action=query, got {result.action}"

            print("   ✅ PASSED - Multi-criteria query executed successfully")
            if duration == 0:
                duration = time.time() - start_time
            self.record_result(
                "Multi-Criteria Query",
                True,
                duration=duration,
                category=self._get_test_category("test_complex_multi_criteria_query"),
                test_method="test_complex_multi_criteria_query",
                query=query,
                steps_taken=steps_taken,
                error_count=error_count,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
            )
            return True

        except Exception as e:
            print(f"   ❌ FAILED - {e}")
            duration = time.time() - start_time
            steps_taken = 0
            error_count = 0
            input_tokens = 0
            output_tokens = 0
            if "result" in locals():
                _, steps_taken, error_count, input_tokens, output_tokens = (
                    self._extract_metadata(result)
                )
            self.record_result(
                "Multi-Criteria Query",
                False,
                str(e),
                duration=duration,
                category=self._get_test_category("test_complex_multi_criteria_query"),
                query=query,
                steps_taken=steps_taken,
                error_count=error_count,
                input_tokens=input_tokens if "input_tokens" in locals() else 0,
                output_tokens=output_tokens if "output_tokens" in locals() else 0,
            )
            return False

    async def test_issue_creation(self) -> bool:
        """Test issue creation with correct parameters."""
        print("\n🧪 Test: Issue Creation")
        import time

        start_time = time.time()

        try:
            timestamp = int(time.time())
            query = f"create an idea called Integration Test Issue {timestamp}"
            if self.json_output:
                result = self._execute_agent_query(query, "test_issue_creation")
                duration, steps_taken, error_count, input_tokens, output_tokens = (
                    self._extract_metadata(result)
                )
            else:
                result = await self.app.execute_command(query)
                duration = time.time() - start_time
                steps_taken = 0
                error_count = 0

            assert result.success, result.error
            assert (
                result.action == "query"
            ), f"Expected action=query, got {result.action}"

            print(f"   ✅ PASSED - Issue created successfully")
            if duration == 0:
                duration = time.time() - start_time
            self.record_result(
                "Issue Creation",
                True,
                duration=duration,
                category=self._get_test_category("test_issue_creation"),
                test_method="test_issue_creation",
                query=query,
                steps_taken=steps_taken,
                error_count=error_count,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
            )
            return True

        except Exception as e:
            print(f"   ❌ FAILED - {e}")
            duration = time.time() - start_time
            steps_taken = 0
            error_count = 0
            input_tokens = 0
            output_tokens = 0
            if "result" in locals():
                _, steps_taken, error_count, input_tokens, output_tokens = (
                    self._extract_metadata(result)
                )
            self.record_result(
                "Issue Creation",
                False,
                str(e),
                duration=duration,
                category=self._get_test_category("test_issue_creation"),
                query=query,
                steps_taken=steps_taken,
                error_count=error_count,
                input_tokens=input_tokens if "input_tokens" in locals() else 0,
                output_tokens=output_tokens if "output_tokens" in locals() else 0,
            )
            return False

    async def test_completion_no_timeout(self) -> bool:
        """Test that queries complete without timeout errors."""
        print("\n🧪 Test: Completion Without Timeout")
        import time

        start_time = time.time()
        query = "search for issues assigned to nonexistentuser@example.com"

        try:
            if self.json_output:
                result = self._execute_agent_query(query, "test_completion_no_timeout")
                duration, steps_taken, error_count, input_tokens, output_tokens = (
                    self._extract_metadata(result)
                )
            else:
                result = await self.app.execute_command(query)
                duration = time.time() - start_time
                steps_taken = 0
                error_count = 0

            assert result.success, result.error
            assert "Maximum steps reached" not in str(
                result.error or ""
            ), "Should not timeout"

            print("   ✅ PASSED - Query completed without timeout")
            if duration == 0:
                duration = time.time() - start_time
            self.record_result(
                "Completion Without Timeout",
                True,
                duration=duration,
                category=self._get_test_category("test_completion_no_timeout"),
                test_method="test_completion_no_timeout",
                query=query,
                steps_taken=steps_taken,
                error_count=error_count,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
            )
            return True

        except Exception as e:
            print(f"   ❌ FAILED - {e}")
            duration = time.time() - start_time
            steps_taken = 0
            error_count = 0
            input_tokens = 0
            output_tokens = 0
            if "result" in locals():
                _, steps_taken, error_count, input_tokens, output_tokens = (
                    self._extract_metadata(result)
                )
            self.record_result(
                "Completion Without Timeout",
                False,
                str(e),
                duration=duration,
                category=self._get_test_category("test_completion_no_timeout"),
                query=query,
                steps_taken=steps_taken,
                error_count=error_count,
                input_tokens=input_tokens if "input_tokens" in locals() else 0,
                output_tokens=output_tokens if "output_tokens" in locals() else 0,
            )
            return False

    async def test_app_methods(self) -> bool:
        """Test app-level methods."""
        print("\n🧪 Test: App Methods")
        import time

        start_time = time.time()

        try:
            # Test search method
            result = await self.app.search("my issues")
            assert result.success, "App search method should work"

            # Test create_issue method
            timestamp = int(time.time())
            result = await self.app.create_issue(
                f"App Method Test {timestamp}", "Test Description", "Idea"
            )
            assert result.success, "App create_issue method should work"

            print("   ✅ PASSED - App methods work correctly")
            duration = time.time() - start_time
            command = (
                f"search('my issues'), create_issue('App Method Test {timestamp}', ...)"
            )
            # App methods don't use agent execution, so no metadata available
            steps_taken = 0
            error_count = 0
            input_tokens = 0
            output_tokens = 0
            self.record_result(
                "App Methods",
                True,
                duration=duration,
                category=self._get_test_category("test_app_methods"),
                test_method="test_app_methods",
                query=command,
                steps_taken=steps_taken,
                error_count=error_count,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
            )
            return True

        except Exception as e:
            print(f"   ❌ FAILED - {e}")
            duration = time.time() - start_time
            steps_taken = 0
            error_count = 0
            input_tokens = 0
            output_tokens = 0
            self.record_result(
                "App Methods",
                False,
                str(e),
                duration=duration,
                category=self._get_test_category("test_app_methods"),
                steps_taken=steps_taken,
                error_count=error_count,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
            )
            return False

    async def test_intermediate_search_queries(self) -> bool:
        """Test intermediate search queries from demo."""
        print("\n🧪 Test: Intermediate Search Queries")

        # Use discovered configuration for dynamic queries
        primary_type = self.issue_types[0] if self.issue_types else "Idea"
        high_priority = (
            "High"
            if "High" in self.priorities
            else (self.priorities[1] if len(self.priorities) > 1 else "High")
        )
        parking_status = (
            "Parking lot"
            if "Parking lot" in self.statuses
            else (self.statuses[0] if self.statuses else "Parking lot")
        )

        queries = [
            f"Show me all {high_priority.lower()} priority {primary_type.lower()}s",
            "Find issues created this week",
            "Search for unassigned critical issues",
            f"Show me {primary_type.lower()}s that are in progress",
            f"Get me all the tickets in {parking_status.lower()} status",
            "Find all issues updated in the last 24 hours",
            "List issues with 'API' in the summary",
        ]

        all_passed = True
        for i, query in enumerate(queries, 1):
            passed = await self._run_query_test(
                query, "Intermediate Query", i, len(queries)
            )
            if not passed:
                all_passed = False

        return all_passed

    async def test_status_priority_queries(self) -> bool:
        """Test status and priority-based queries from demo."""
        print("\n🧪 Test: Status & Priority Queries")

        queries = [
            "Show me all issues in parking lot status",
            "Find all critical priority issues",
            "Get issues that are blocked or have blocker priority",
            "Show me ideas that are not yet assigned",
            "Find all issues with no fix version set",
            "Search for issues in draft or to do status",
            "List all high priority bugs in progress",
            "Show me issues assigned to currentUser()",
            "Find unresolved issues with due dates in the past",
            "Get all issues where resolution is empty",
        ]

        all_passed = True
        for i, query in enumerate(queries, 1):
            passed = await self._run_query_test(
                query, "Status/Priority Query", i, len(queries)
            )
            if not passed:
                all_passed = False

        return all_passed

    async def test_advanced_jql_queries(self) -> bool:
        """Test advanced JQL query generation from demo."""
        print("\n🧪 Test: Advanced JQL Queries")
        import time

        time.time()

        queries = [
            "Show me issues assigned to me that were created last month and are still open",
            "Find bugs in project ABC or DEV that have high or critical priority",
            "List all stories in the current sprint that are not done",
            "Show issues where I'm mentioned in comments",
            "Find all blockers assigned to the backend team",
            "Search for issues with attachments that were resolved this week",
        ]

        all_passed = True
        for i, query in enumerate(queries, 1):
            query_start = time.time()
            try:
                if self.json_output:
                    result = self._execute_agent_query(
                        query, "test_advanced_jql_queries"
                    )
                    _, steps_taken, error_count, input_tokens, output_tokens = (
                        self._extract_metadata(result)
                    )
                else:
                    result = await self.app.execute_command(query)
                    steps_taken = 0
                    error_count = 0
                    input_tokens = 0
                    output_tokens = 0

                assert result.success, result.error
                if hasattr(result, "action"):
                    assert (
                        result.action == "query"
                    ), f"Expected action=query for '{query}', got {result.action}"

                query_duration = time.time() - query_start
                self.record_result(
                    f"Advanced JQL Query {i}/{len(queries)}",
                    True,
                    duration=query_duration,
                    category=self._get_test_category("test_advanced_jql_queries"),
                    test_method="test_advanced_jql_queries",
                    query=query,
                    steps_taken=steps_taken,
                    error_count=error_count,
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                )
                query_preview = str(query)[:50] if query else "unknown query"
                print(f"   ✅ Query {i}/{len(queries)}: {query_preview}... - PASSED")

            except Exception as e:
                all_passed = False
                query_duration = time.time() - query_start
                self.record_result(
                    f"Advanced JQL Query {i}/{len(queries)}",
                    False,
                    str(e),
                    duration=query_duration,
                    category=self._get_test_category("test_advanced_jql_queries"),
                    query=query,
                    steps_taken=0,
                    error_count=0,
                    input_tokens=0,
                    output_tokens=0,
                    test_method="test_advanced_jql_queries",
                )
                query_preview = str(query)[:50] if query else "unknown query"
                print(
                    f"   ❌ Query {i}/{len(queries)}: {query_preview}... - FAILED: {e}"
                )

        return all_passed

    async def test_time_based_analysis(self) -> bool:
        """Test time-based analysis queries from demo."""
        print("\n🧪 Test: Time-Based Analysis")
        import time

        time.time()

        queries = [
            "Show me issues created in the last 2 weeks but not updated since",
            "Find issues that have been in progress for more than 30 days",
            "Get all issues resolved yesterday",
            "Show me issues created this month grouped by week",
            "Find issues with due dates in the next 7 days",
            "Show me issues that were reopened more than once",
            "Find issues created on weekends",
            "Show me the oldest unresolved critical issues",
        ]

        all_passed = True
        for i, query in enumerate(queries, 1):
            query_start = time.time()
            try:
                if self.json_output:
                    result = self._execute_agent_query(
                        query, "test_time_based_analysis"
                    )
                    _, steps_taken, error_count, input_tokens, output_tokens = (
                        self._extract_metadata(result)
                    )
                else:
                    result = await self.app.execute_command(query)
                    steps_taken = 0
                    error_count = 0
                    input_tokens = 0
                    output_tokens = 0

                assert result.success, result.error
                if hasattr(result, "action"):
                    assert (
                        result.action == "query"
                    ), f"Expected action=query for '{query}', got {result.action}"

                query_duration = time.time() - query_start
                self.record_result(
                    f"Time Analysis Query {i}/{len(queries)}",
                    True,
                    duration=query_duration,
                    category=self._get_test_category("test_time_based_analysis"),
                    test_method="test_time_based_analysis",
                    query=query,
                    steps_taken=steps_taken,
                    error_count=error_count,
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                )
                query_preview = str(query)[:50] if query else "unknown query"
                print(f"   ✅ Query {i}/{len(queries)}: {query_preview}... - PASSED")

            except Exception as e:
                all_passed = False
                query_duration = time.time() - query_start
                self.record_result(
                    f"Time Analysis Query {i}/{len(queries)}",
                    False,
                    str(e),
                    duration=query_duration,
                    category=self._get_test_category("test_time_based_analysis"),
                    test_method="test_time_based_analysis",
                    query=query,
                    steps_taken=0,
                    error_count=0,
                    input_tokens=0,
                    output_tokens=0,
                )
                query_preview = str(query)[:50] if query else "unknown query"
                print(
                    f"   ❌ Query {i}/{len(queries)}: {query_preview}... - FAILED: {e}"
                )

        return all_passed

    async def test_complex_filters(self) -> bool:
        """Test complex filtering queries from demo."""
        print("\n🧪 Test: Complex Filters")
        import time

        time.time()

        queries = [
            "Show me critical or blocker issues that have been open for more than 30 days",
            "Find all issues labeled with 'security' or 'vulnerability' that are not resolved",
            "List bugs assigned to me or my team that have no fix version",
            "Show stories with story points greater than 8 that are in the backlog",
            "Find issues with due date this week that are not in progress",
            "Search for bugs reported by customers that have no assignee",
        ]

        all_passed = True
        for i, query in enumerate(queries, 1):
            query_start = time.time()
            try:
                if self.json_output:
                    result = self._execute_agent_query(query, "test_complex_filters")
                    _, steps_taken, error_count, input_tokens, output_tokens = (
                        self._extract_metadata(result)
                    )
                else:
                    result = await self.app.execute_command(query)
                    steps_taken = 0
                    error_count = 0
                    input_tokens = 0
                    output_tokens = 0

                assert result.success, result.error
                if hasattr(result, "action"):
                    assert (
                        result.action == "query"
                    ), f"Expected action=query for '{query}', got {result.action}"

                query_duration = time.time() - query_start
                self.record_result(
                    f"Complex Filter Query {i}/{len(queries)}",
                    True,
                    duration=query_duration,
                    category=self._get_test_category("test_complex_filters"),
                    test_method="test_complex_filters",
                    query=query,
                    steps_taken=steps_taken,
                    error_count=error_count,
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                )
                query_preview = str(query)[:50] if query else "unknown query"
                print(f"   ✅ Query {i}/{len(queries)}: {query_preview}... - PASSED")

            except Exception as e:
                all_passed = False
                query_duration = time.time() - query_start
                self.record_result(
                    f"Complex Filter Query {i}/{len(queries)}",
                    False,
                    str(e),
                    duration=query_duration,
                    category=self._get_test_category("test_complex_filters"),
                    test_method="test_complex_filters",
                    query=query,
                    steps_taken=0,
                    error_count=0,
                    input_tokens=0,
                    output_tokens=0,
                )
                query_preview = str(query)[:50] if query else "unknown query"
                print(
                    f"   ❌ Query {i}/{len(queries)}: {query_preview}... - FAILED: {e}"
                )

        return all_passed

    async def test_cross_project_analysis(self) -> bool:
        """Test cross-project analysis queries from demo."""
        print("\n🧪 Test: Cross-Project Analysis")
        import time

        time.time()

        queries = [
            "Compare bug rates across all projects in the last month",
            "Find dependencies between issues in different projects",
            "Show me all critical issues across projects assigned to the security team",
            "Identify duplicate issues with similar summaries across projects",
            "Find all issues linking to external systems or URLs",
            "Get all issues with the same epic key across different projects",
            "Show me all projects with overdue releases",
        ]

        all_passed = True
        for i, query in enumerate(queries, 1):
            query_start = time.time()
            try:
                if self.json_output:
                    result = self._execute_agent_query(
                        query, "test_cross_project_analysis"
                    )
                    _, steps_taken, error_count, input_tokens, output_tokens = (
                        self._extract_metadata(result)
                    )
                else:
                    result = await self.app.execute_command(query)
                    steps_taken = 0
                    error_count = 0
                    input_tokens = 0
                    output_tokens = 0

                assert result.success, result.error
                if hasattr(result, "action"):
                    assert (
                        result.action == "query"
                    ), f"Expected action=query for '{query}', got {result.action}"

                query_duration = time.time() - query_start
                self.record_result(
                    f"Cross-Project Query {i}/{len(queries)}",
                    True,
                    duration=query_duration,
                    category=self._get_test_category("test_cross_project_analysis"),
                    test_method="test_cross_project_analysis",
                    query=query,
                    steps_taken=steps_taken,
                    error_count=error_count,
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                )
                query_preview = str(query)[:50] if query else "unknown query"
                print(f"   ✅ Query {i}/{len(queries)}: {query_preview}... - PASSED")

            except Exception as e:
                all_passed = False
                query_duration = time.time() - query_start
                self.record_result(
                    f"Cross-Project Query {i}/{len(queries)}",
                    False,
                    str(e),
                    duration=query_duration,
                    category=self._get_test_category("test_cross_project_analysis"),
                    test_method="test_cross_project_analysis",
                    query=query,
                    steps_taken=0,
                    error_count=0,
                    input_tokens=0,
                    output_tokens=0,
                )
                query_preview = str(query)[:50] if query else "unknown query"
                print(
                    f"   ❌ Query {i}/{len(queries)}: {query_preview}... - FAILED: {e}"
                )

        return all_passed

    async def test_complex_business_logic(self) -> bool:
        """Test complex business logic queries from demo."""
        print("\n🧪 Test: Complex Business Logic")
        import time

        time.time()

        queries = [
            "Find all issues where story points are set but the issue is not estimated (missing time tracking)",
            "Show me bugs that were resolved but then had new bugs created referencing them",
            "Get all features that are 'Done' but their child tasks are still 'In Progress'",
            "Find issues assigned to inactive users (not logged in for 30+ days)",
            "Show me all epics where less than 50% of child issues are completed",
            "Identify issues that have been moved between projects more than once",
            "Show me issues where the assignee changed more than 3 times",
            "Find issues created by external users (not in our organization domain)",
        ]

        all_passed = True
        for i, query in enumerate(queries, 1):
            query_start = time.time()
            try:
                if self.json_output:
                    result = self._execute_agent_query(
                        query, "test_complex_business_logic"
                    )
                    _, steps_taken, error_count, input_tokens, output_tokens = (
                        self._extract_metadata(result)
                    )
                else:
                    result = await self.app.execute_command(query)
                    steps_taken = 0
                    error_count = 0
                    input_tokens = 0
                    output_tokens = 0

                assert result.success, result.error
                if hasattr(result, "action"):
                    assert (
                        result.action == "query"
                    ), f"Expected action=query for '{query}', got {result.action}"

                query_duration = time.time() - query_start
                self.record_result(
                    f"Business Logic Query {i}/{len(queries)}",
                    True,
                    duration=query_duration,
                    category=self._get_test_category("test_complex_business_logic"),
                    test_method="test_complex_business_logic",
                    query=query,
                    steps_taken=steps_taken,
                    error_count=error_count,
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                )
                query_preview = str(query)[:50] if query else "unknown query"
                print(f"   ✅ Query {i}/{len(queries)}: {query_preview}... - PASSED")

            except Exception as e:
                all_passed = False
                query_duration = time.time() - query_start
                self.record_result(
                    f"Business Logic Query {i}/{len(queries)}",
                    False,
                    str(e),
                    duration=query_duration,
                    category=self._get_test_category("test_complex_business_logic"),
                    test_method="test_complex_business_logic",
                    query=query,
                    steps_taken=0,
                    error_count=0,
                    input_tokens=0,
                    output_tokens=0,
                )
                query_preview = str(query)[:50] if query else "unknown query"
                print(
                    f"   ❌ Query {i}/{len(queries)}: {query_preview}... - FAILED: {e}"
                )

        return all_passed

    async def test_analytics_queries(self) -> bool:
        """Test analytics and summarization queries from demo."""
        print("\n🧪 Test: Analytics Queries")
        import time

        time.time()

        queries = [
            "How many issues were resolved this week?",
            "What's the breakdown of issue types in my project?",
            "Show me the team's velocity over the last 3 sprints",
            "Analyze bug trends over the last quarter and identify problem areas",
            "Calculate average resolution time for critical bugs vs normal bugs",
            "What percentage of issues miss their due date?",
            "Summarize my team's accomplishments this sprint",
            "What are the top 5 blockers affecting the team?",
            "Based on current velocity, when will we complete the backlog?",
        ]

        all_passed = True
        for i, query in enumerate(queries, 1):
            query_start = time.time()
            try:
                if self.json_output:
                    result = self._execute_agent_query(query, "test_analytics_queries")
                    _, steps_taken, error_count, input_tokens, output_tokens = (
                        self._extract_metadata(result)
                    )
                else:
                    result = await self.app.execute_command(query)
                    steps_taken = 0
                    error_count = 0
                    input_tokens = 0
                    output_tokens = 0

                assert result.success, result.error
                if hasattr(result, "action"):
                    assert (
                        result.action == "query"
                    ), f"Expected action=query for '{query}', got {result.action}"

                query_duration = time.time() - query_start
                self.record_result(
                    f"Analytics Query {i}/{len(queries)}",
                    True,
                    duration=query_duration,
                    category=self._get_test_category("test_analytics_queries"),
                    test_method="test_analytics_queries",
                    query=query,
                    steps_taken=steps_taken,
                    error_count=error_count,
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                )
                query_preview = str(query)[:50] if query else "unknown query"
                print(f"   ✅ Query {i}/{len(queries)}: {query_preview}... - PASSED")

            except Exception as e:
                all_passed = False
                query_duration = time.time() - query_start
                self.record_result(
                    f"Analytics Query {i}/{len(queries)}",
                    False,
                    str(e),
                    duration=query_duration,
                    category=self._get_test_category("test_analytics_queries"),
                    test_method="test_analytics_queries",
                    query=query,
                    steps_taken=0,
                    error_count=0,
                    input_tokens=0,
                    output_tokens=0,
                )
                query_preview = str(query)[:50] if query else "unknown query"
                print(
                    f"   ❌ Query {i}/{len(queries)}: {query_preview}... - FAILED: {e}"
                )

        return all_passed

    async def test_bulk_operations(self) -> bool:
        """Test bulk processing capabilities from demo."""
        print("\n🧪 Test: Bulk Operations")
        import time

        start_time = time.time()

        try:
            # Test bulk creation from meeting notes
            meeting_notes = """
            Sprint Planning Meeting - Action Items:
            1. Test User: Fix authentication bug (critical)
            2. Dev Team: Update API documentation  
            3. QA Team: Review security audit findings
            """

            results = await self.app.bulk_create_from_notes(meeting_notes)
            assert len(results) > 0, "Should create multiple issues from notes"

            for result in results:
                assert result.success, result.error

            print("   ✅ PASSED - Bulk operations executed successfully")
            duration = time.time() - start_time
            # Calculate line count outside f-string to avoid backslash issue
            line_count = len(meeting_notes.strip().split("\n"))
            command = f"bulk_create_from_notes({line_count} action items)"
            # Bulk operations don't use agent execution, so no metadata available
            steps_taken = 0
            error_count = 0
            input_tokens = 0
            output_tokens = 0
            self.record_result(
                "Bulk Operations",
                True,
                duration=duration,
                category=self._get_test_category("test_bulk_operations"),
                test_method="test_bulk_operations",
                query=command,
                steps_taken=steps_taken,
                error_count=error_count,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
            )
            return True

        except Exception as e:
            print(f"   ❌ FAILED - {e}")
            duration = time.time() - start_time
            steps_taken = 0
            error_count = 0
            input_tokens = 0
            output_tokens = 0
            self.record_result(
                "Bulk Operations",
                False,
                str(e),
                duration=duration,
                category=self._get_test_category("test_bulk_operations"),
                steps_taken=steps_taken,
                error_count=error_count,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
            )
            return False

    async def test_advanced_creation_patterns(self) -> bool:
        """Test advanced issue creation patterns from demo."""
        print("\n🧪 Test: Advanced Creation Patterns")
        import time

        start_time = time.time()

        try:
            timestamp = int(time.time())

            creation_commands = [
                f"Create a task called 'Test Review Code {timestamp}'",
                f"Create a high priority bug titled 'Test Database Timeout {timestamp}'",
                f"Create a critical bug titled 'Test Security Issue {timestamp}' with description 'Test vulnerability found in input fields'",
            ]

            all_passed = True
            for i, command in enumerate(creation_commands, 1):
                command_start = time.time()
                try:
                    if self.json_output:
                        result = self._execute_agent_query(
                            command, "test_advanced_creation_patterns"
                        )
                        _, steps_taken, error_count, input_tokens, output_tokens = (
                            self._extract_metadata(result)
                        )
                    else:
                        result = await self.app.execute_command(command)
                        steps_taken = 0
                        error_count = 0
                        input_tokens = 0
                        output_tokens = 0

                    assert result.success, result.error
                    assert (
                        result.action == "query"
                    ), f"Expected action=query for '{command}', got {result.action}"

                    command_duration = time.time() - command_start
                    self.record_result(
                        f"Creation Pattern {i}/{len(creation_commands)}",
                        True,
                        duration=command_duration,
                        category=self._get_test_category(
                            "test_advanced_creation_patterns"
                        ),
                        query=command,
                        steps_taken=steps_taken,
                        error_count=error_count,
                        input_tokens=input_tokens,
                        output_tokens=output_tokens,
                        test_method="test_advanced_creation_patterns",
                    )
                    print(
                        f"   ✅ Creation {i}/{len(creation_commands)}: {command[:50]}... - PASSED"
                    )

                except Exception as e:
                    all_passed = False
                    command_duration = time.time() - command_start
                    self.record_result(
                        f"Creation Pattern {i}/{len(creation_commands)}",
                        False,
                        str(e),
                        duration=command_duration,
                        category=self._get_test_category(
                            "test_advanced_creation_patterns"
                        ),
                        query=command,
                        steps_taken=0,
                        error_count=0,
                        input_tokens=0,
                        output_tokens=0,
                        test_method="test_advanced_creation_patterns",
                    )
                    print(
                        f"   ❌ Creation {i}/{len(creation_commands)}: {command[:50]}... - FAILED: {e}"
                    )

            return all_passed

        except Exception as e:
            print(f"   ❌ FAILED - Setup error: {e}")
            duration = time.time() - start_time
            self.record_result(
                "Advanced Creation Patterns Setup",
                False,
                str(e),
                duration=duration,
                category=self._get_test_category("test_advanced_creation_patterns"),
                steps_taken=0,
                error_count=0,
                input_tokens=0,
                output_tokens=0,
                test_method="test_advanced_creation_patterns",
            )
            return False

    async def test_multi_step_workflows(self) -> bool:
        """Test complex multi-step workflow queries from demo."""
        print("\n🧪 Test: Multi-Step Workflows")
        import time

        time.time()

        workflows = [
            "Find all critical bugs from last sprint, summarize them, and create a report page in Confluence",
            "Get all unresolved issues assigned to me, prioritize them by due date, and create a daily task list",
            "Search for issues with missing estimates, group them by component, and create tasks to add estimates",
            "Find duplicate issues based on summary similarity and create a cleanup task",
        ]

        all_passed = True
        for i, item in enumerate(workflows, 1):
            item_start = time.time()
            try:
                if self.json_output:
                    result = self._execute_agent_query(
                        item, "test_multi_step_workflows"
                    )
                    _, steps_taken, error_count, input_tokens, output_tokens = (
                        self._extract_metadata(result)
                    )
                else:
                    result = await self.app.execute_command(item)
                    steps_taken = 0
                    error_count = 0
                    input_tokens = 0
                    output_tokens = 0

                assert result.success, result.error

                item_duration = time.time() - item_start
                self.record_result(
                    f"Workflow {i}/{len(workflows)}",
                    True,
                    duration=item_duration,
                    category=self._get_test_category("test_multi_step_workflows"),
                    query=str(item),
                    steps_taken=steps_taken,
                    error_count=error_count,
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                )
                print(
                    f"   ✅ Workflow {i}/{len(workflows)}: {str(item)[:50]}... - PASSED"
                )

            except Exception as e:
                all_passed = False
                item_duration = time.time() - item_start
                self.record_result(
                    f"Workflow {i}/{len(workflows)}",
                    False,
                    str(e),
                    duration=item_duration,
                    category=self._get_test_category("test_multi_step_workflows"),
                    query=str(item),
                    steps_taken=0,
                    error_count=0,
                    input_tokens=0,
                    output_tokens=0,
                )
                print(
                    f"   ❌ Workflow {i}/{len(workflows)}: {str(item)[:50]}... - FAILED: {e}"
                )

        return all_passed

    async def test_advanced_automation_scenarios(self) -> bool:
        """Test advanced automation scenarios from demo."""
        print("\n🧪 Test: Advanced Automation Scenarios")
        import time

        time.time()

        scenarios = [
            "Find all issues with missing descriptions and auto-generate template descriptions based on issue type",
            "Bulk update all 'To Do' issues older than 90 days to 'Parking Lot' status",
            "Create subtasks for all epic issues that don't have any child issues",
            "Auto-assign issues to team leads based on component ownership rules",
            "Find all features missing test cases and create 'Create Tests' subtasks for each",
            "Create deployment tickets for all features marked as 'Ready for Release'",
        ]

        all_passed = True
        for i, scenario in enumerate(scenarios, 1):
            scenario_start = time.time()
            try:
                if self.json_output:
                    result = self._execute_agent_query(
                        scenario, "test_advanced_automation_scenarios"
                    )
                    _, steps_taken, error_count, input_tokens, output_tokens = (
                        self._extract_metadata(result)
                    )
                else:
                    result = await self.app.execute_command(scenario)
                    steps_taken = 0
                    error_count = 0
                    input_tokens = 0
                    output_tokens = 0

                assert result.success, result.error
                assert (
                    result.action == "query"
                ), f"Expected action=query for '{scenario}', got {result.action}"

                scenario_duration = time.time() - scenario_start
                self.record_result(
                    f"Automation Scenario {i}/{len(scenarios)}",
                    True,
                    duration=scenario_duration,
                    category=self._get_test_category(
                        "test_advanced_automation_scenarios"
                    ),
                    test_method="test_advanced_automation_scenarios",
                    query=scenario,
                    steps_taken=steps_taken,
                    error_count=error_count,
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                )
                print(
                    f"   ✅ Scenario {i}/{len(scenarios)}: {scenario[:50]}... - PASSED"
                )

            except Exception as e:
                all_passed = False
                scenario_duration = time.time() - scenario_start
                self.record_result(
                    f"Automation Scenario {i}/{len(scenarios)}",
                    False,
                    str(e),
                    duration=scenario_duration,
                    category=self._get_test_category(
                        "test_advanced_automation_scenarios"
                    ),
                    test_method="test_advanced_automation_scenarios",
                    query=scenario,
                    steps_taken=0,
                    error_count=0,
                    input_tokens=0,
                    output_tokens=0,
                )
                print(
                    f"   ❌ Scenario {i}/{len(scenarios)}: {scenario[:50]}... - FAILED: {e}"
                )

        return all_passed

    async def test_predictive_analysis_queries(self) -> bool:
        """Test predictive analysis queries from demo."""
        print("\n🧪 Test: Predictive Analysis Queries")
        import time

        time.time()

        predictions = [
            "Predict which issues are at risk of missing their deadlines based on current progress",
            "Identify issues likely to be reopened based on historical patterns",
            "Forecast sprint completion probability based on current velocity",
            "Predict which bugs are likely to be critical based on description and component",
            "Estimate effort required for unestimated issues using similar historical issues",
            "Identify recurring bug patterns that suggest systemic issues",
            "Find anomalies in issue creation patterns that might indicate problems",
            "Predict optimal assignee for new issues based on expertise and workload",
        ]

        all_passed = True
        for i, item in enumerate(predictions, 1):
            item_start = time.time()
            try:
                if self.json_output:
                    result = self._execute_agent_query(
                        item, "test_predictive_analysis_queries"
                    )
                    _, steps_taken, error_count, input_tokens, output_tokens = (
                        self._extract_metadata(result)
                    )
                else:
                    result = await self.app.execute_command(item)
                    steps_taken = 0
                    error_count = 0
                    input_tokens = 0
                    output_tokens = 0

                assert result.success, result.error

                item_duration = time.time() - item_start
                self.record_result(
                    f"Prediction {i}/{len(predictions)}",
                    True,
                    duration=item_duration,
                    category=self._get_test_category(
                        "test_predictive_analysis_queries"
                    ),
                    test_method="test_predictive_analysis_queries",
                    query=str(item),
                    steps_taken=steps_taken,
                    error_count=error_count,
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                )
                print(
                    f"   ✅ Prediction {i}/{len(predictions)}: {str(item)[:50]}... - PASSED"
                )

            except Exception as e:
                all_passed = False
                item_duration = time.time() - item_start
                self.record_result(
                    f"Prediction {i}/{len(predictions)}",
                    False,
                    str(e),
                    duration=item_duration,
                    category=self._get_test_category(
                        "test_predictive_analysis_queries"
                    ),
                    test_method="test_predictive_analysis_queries",
                    query=str(item),
                    steps_taken=0,
                    error_count=0,
                    input_tokens=0,
                    output_tokens=0,
                )
                print(
                    f"   ❌ Prediction {i}/{len(predictions)}: {str(item)[:50]}... - FAILED: {e}"
                )

        return all_passed

    async def test_basic_fetch_queries(self) -> bool:
        """Test basic fetch queries from demo."""
        print("\n🧪 Test: Basic Fetch Queries")
        import time

        time.time()

        queries = [
            "Show me all my issues",
            "What issues are assigned to me?",
            "List all open issues",
            f"Show me issues in project {self.project_keys[0] if self.project_keys else 'MDP'}",
            "What are the recent issues?",
        ]

        all_passed = True
        for i, query in enumerate(queries, 1):
            query_start = time.time()
            try:
                if self.json_output:
                    result = self._execute_agent_query(
                        query, "test_basic_fetch_queries"
                    )
                    _, steps_taken, error_count, input_tokens, output_tokens = (
                        self._extract_metadata(result)
                    )
                else:
                    result = await self.app.execute_command(query)
                    steps_taken = 0
                    error_count = 0
                    input_tokens = 0
                    output_tokens = 0

                assert result.success, result.error
                if hasattr(result, "action"):
                    assert (
                        result.action == "query"
                    ), f"Expected action=query for '{query}', got {result.action}"

                query_duration = time.time() - query_start
                self.record_result(
                    f"Basic Fetch Query {i}/{len(queries)}",
                    True,
                    duration=query_duration,
                    category=self._get_test_category("test_basic_fetch_queries"),
                    query=query,
                    steps_taken=steps_taken,
                    error_count=error_count,
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    test_method="test_basic_fetch_queries",
                )
                query_preview = str(query)[:50] if query else "unknown query"
                print(f"   ✅ Query {i}/{len(queries)}: {query_preview}... - PASSED")

            except Exception as e:
                all_passed = False
                query_duration = time.time() - query_start
                self.record_result(
                    f"Basic Fetch Query {i}/{len(queries)}",
                    False,
                    str(e),
                    duration=query_duration,
                    category=self._get_test_category("test_basic_fetch_queries"),
                    query=query,
                    steps_taken=0,
                    error_count=0,
                    input_tokens=0,
                    output_tokens=0,
                    test_method="test_basic_fetch_queries",
                )
                query_preview = str(query)[:50] if query else "unknown query"
                print(
                    f"   ❌ Query {i}/{len(queries)}: {query_preview}... - FAILED: {e}"
                )

        return all_passed

    async def test_missing_advanced_queries(self) -> bool:
        """Test advanced queries missing from other test methods."""
        print("\n🧪 Test: Missing Advanced Queries")
        import time

        time.time()

        queries = [
            # Additional time-based queries from demo
            "Search for issues updated during business hours only (9-5 weekdays)",
            "Get issues resolved faster than average for their priority",
            "Find issues where time to resolution exceeded SLA",
            "Analyze resolution time trends by issue type over last quarter",
            # Additional complex business logic queries
            "Find all issues with custom field 'Business Impact' set to 'High' but priority is 'Low'",
            "Get all issues with attachments larger than 10MB",
            "Show me all issues where due date was extended multiple times",
            "Identify 'zombie' issues - reopened more than 3 times with same resolution",
            # Additional cross-project queries
            "Show me cross-team collaboration issues (multiple assignees from different teams)",
            "Find issues that mention other project keys in comments or descriptions",
            "Identify projects with the highest technical debt (based on bug-to-feature ratio)",
            "Find all issues affecting multiple environments or platforms",
            "Show me security vulnerabilities across all projects with their current status",
            # Additional automation scenarios
            "Generate weekly summary reports for each project and create Confluence pages",
            "For all critical bugs: create a hotfix branch, assign to senior dev, set due date to tomorrow",
            "Auto-close all resolved issues older than 30 days and notify assignees",
            "Generate release notes from all resolved issues in the current fixVersion",
            "Create executive dashboard showing KPIs across all projects",
            "Generate technical debt report identifying refactoring candidates",
            "Create capacity planning report based on team velocity and backlog size",
        ]

        all_passed = True
        for i, query in enumerate(queries, 1):
            query_start = time.time()
            try:
                if self.json_output:
                    result = self._execute_agent_query(
                        query, "test_missing_advanced_queries"
                    )
                    _, steps_taken, error_count, input_tokens, output_tokens = (
                        self._extract_metadata(result)
                    )
                else:
                    result = await self.app.execute_command(query)
                    steps_taken = 0
                    error_count = 0
                    input_tokens = 0
                    output_tokens = 0

                assert result.success, result.error
                if hasattr(result, "action"):
                    assert (
                        result.action == "query"
                    ), f"Expected action=query for '{query}', got {result.action}"

                query_duration = time.time() - query_start
                self.record_result(
                    f"Advanced Query {i}/{len(queries)}",
                    True,
                    duration=query_duration,
                    category=self._get_test_category("test_missing_advanced_queries"),
                    query=query,
                    steps_taken=steps_taken,
                    error_count=error_count,
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    test_method="test_missing_advanced_queries",
                )
                query_preview = str(query)[:50] if query else "unknown query"
                print(f"   ✅ Query {i}/{len(queries)}: {query_preview}... - PASSED")

            except Exception as e:
                all_passed = False
                query_duration = time.time() - query_start
                self.record_result(
                    f"Advanced Query {i}/{len(queries)}",
                    False,
                    str(e),
                    duration=query_duration,
                    category=self._get_test_category("test_missing_advanced_queries"),
                    query=query,
                    steps_taken=0,
                    error_count=0,
                    input_tokens=0,
                    output_tokens=0,
                    test_method="test_missing_advanced_queries",
                )
                query_preview = str(query)[:50] if query else "unknown query"
                print(
                    f"   ❌ Query {i}/{len(queries)}: {query_preview}... - FAILED: {e}"
                )

        return all_passed

    async def test_strategic_insights_queries(self) -> bool:
        """Test strategic insights and predictive queries missing from other tests."""
        print("\n🧪 Test: Strategic Insights Queries")
        import time

        time.time()

        queries = [
            # Additional predictive analysis queries
            "Identify team burnout risk based on issue assignment and velocity trends",
            "Forecast when technical debt issues will become critical blockers",
            "Analyze feature request patterns to predict market demands",
            "Identify which components need architectural review based on bug density",
            "Predict resource needs for upcoming sprints based on backlog complexity",
            "Generate recommendations for process improvements based on workflow analysis",
            "Identify knowledge gaps in the team based on issue assignment patterns",
            # Additional analytics queries
            "Give me an executive summary of project health",
            "Identify issues at risk of missing their deadline",
        ]

        all_passed = True
        for i, query in enumerate(queries, 1):
            query_start = time.time()
            try:
                if self.json_output:
                    result = self._execute_agent_query(
                        query, "test_strategic_insights_queries"
                    )
                    _, steps_taken, error_count, input_tokens, output_tokens = (
                        self._extract_metadata(result)
                    )
                else:
                    result = await self.app.execute_command(query)
                    steps_taken = 0
                    error_count = 0
                    input_tokens = 0
                    output_tokens = 0

                assert result.success, result.error
                if hasattr(result, "action"):
                    assert (
                        result.action == "query"
                    ), f"Expected action=query for '{query}', got {result.action}"

                query_duration = time.time() - query_start
                self.record_result(
                    f"Strategic Query {i}/{len(queries)}",
                    True,
                    duration=query_duration,
                    category=self._get_test_category("test_strategic_insights_queries"),
                    query=query,
                    steps_taken=steps_taken,
                    error_count=error_count,
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                )
                query_preview = str(query)[:50] if query else "unknown query"
                print(f"   ✅ Query {i}/{len(queries)}: {query_preview}... - PASSED")

            except Exception as e:
                all_passed = False
                query_duration = time.time() - query_start
                self.record_result(
                    f"Strategic Query {i}/{len(queries)}",
                    False,
                    str(e),
                    duration=query_duration,
                    category=self._get_test_category("test_strategic_insights_queries"),
                    query=query,
                    steps_taken=0,
                    error_count=0,
                    input_tokens=0,
                    output_tokens=0,
                )
                query_preview = str(query)[:50] if query else "unknown query"
                print(
                    f"   ❌ Query {i}/{len(queries)}: {query_preview}... - FAILED: {e}"
                )

        return all_passed

    def _write_final_summary(self):
        """Write final summary to the CSV file."""
        if not self.csv_output or not self.csv_file_initialized:
            return

        try:
            with open(self.csv_output, "a", newline="", encoding="utf-8") as csvfile:
                # Calculate summary statistics
                total_tests = len(self.test_results)
                passed = sum(1 for r in self.test_results if r["success"])
                failed = total_tests - passed
                success_rate = (passed / total_tests * 100) if total_tests > 0 else 0

                # Calculate executive metrics
                total_duration = (
                    (self.test_end_time - self.test_start_time).total_seconds()
                    if self.test_end_time and self.test_start_time
                    else 0
                )
                avg_duration = (
                    sum(r.get("duration", 0) for r in self.test_results) / total_tests
                    if total_tests > 0
                    else 0
                )
                total_steps = sum(r.get("steps_taken", 0) for r in self.test_results)
                total_errors = sum(r.get("error_count", 0) for r in self.test_results)
                avg_steps = total_steps / total_tests if total_tests > 0 else 0

                # Token metrics
                total_input_tokens = sum(
                    r.get("input_tokens", 0) for r in self.test_results
                )
                total_output_tokens = sum(
                    r.get("output_tokens", 0) for r in self.test_results
                )
                total_tokens = total_input_tokens + total_output_tokens
                avg_tokens_per_test = (
                    total_tokens / total_tests if total_tests > 0 else 0
                )

                # Group by category
                category_stats = {}
                for result in self.test_results:
                    cat = result.get("category", "Uncategorized")
                    if cat not in category_stats:
                        category_stats[cat] = {
                            "total": 0,
                            "passed": 0,
                            "failed": 0,
                            "duration": 0,
                            "steps": 0,
                        }
                    category_stats[cat]["total"] += 1
                    category_stats[cat]["duration"] += result.get("duration", 0)
                    category_stats[cat]["steps"] += result.get("steps_taken", 0)
                    if result["success"]:
                        category_stats[cat]["passed"] += 1
                    else:
                        category_stats[cat]["failed"] += 1

                # Write separator and executive summary
                csvfile.write("\n\n")
                csvfile.write("=" * 80 + "\n")
                csvfile.write("EXECUTIVE SUMMARY\n")
                csvfile.write("=" * 80 + "\n")
                csvfile.write(
                    f"Test Run Completed: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
                )
                csvfile.write(
                    f"Total Duration: {total_duration:.1f} seconds ({total_duration/60:.1f} minutes)\n"
                )
                csvfile.write("\n")

                # Key Performance Indicators
                csvfile.write("KEY PERFORMANCE INDICATORS\n")
                csvfile.write("-" * 40 + "\n")
                csvfile.write(f"Overall Success Rate: {success_rate:.1f}%\n")
                csvfile.write(f"Total Tests Executed: {total_tests}\n")
                csvfile.write(f"Tests Passed: {passed}\n")
                csvfile.write(f"Tests Failed: {failed}\n")
                csvfile.write(f"Average Test Duration: {avg_duration:.2f} seconds\n")
                csvfile.write(f"Total Agent Steps: {total_steps}\n")
                csvfile.write(f"Average Steps per Test: {avg_steps:.1f}\n")
                csvfile.write(f"Total Errors Encountered: {total_errors}\n")
                csvfile.write("\n")

                # Token Usage Metrics
                csvfile.write("TOKEN USAGE METRICS\n")
                csvfile.write("-" * 40 + "\n")
                csvfile.write(f"Total Input Tokens: {total_input_tokens:,}\n")
                csvfile.write(f"Total Output Tokens: {total_output_tokens:,}\n")
                csvfile.write(f"Total Tokens Used: {total_tokens:,}\n")
                csvfile.write(f"Average Tokens per Test: {avg_tokens_per_test:.0f}\n")
                if total_duration > 0:
                    tokens_per_second = total_tokens / total_duration
                    csvfile.write(f"Tokens per Second: {tokens_per_second:.1f}\n")
                csvfile.write("\n")

                # Category Performance Analysis
                csvfile.write("CATEGORY PERFORMANCE ANALYSIS\n")
                csvfile.write("-" * 40 + "\n")
                csvfile.write(
                    f"{'Category':<30} {'Pass Rate':<12} {'Avg Duration':<15} {'Avg Steps':<10}\n"
                )
                csvfile.write("-" * 70 + "\n")

                # Define logical category order (from basic to advanced)
                category_order = [
                    "Core Functionality",
                    "Basic Operations",
                    "Intermediate Queries",
                    "Advanced Operations",
                    "Expert Features",
                    "Master Level",
                    "Comprehensive Coverage",
                ]

                # Output categories in logical order, then any remaining ones
                displayed_cats = set()
                for cat in category_order:
                    if cat in category_stats:
                        stats = category_stats[cat]
                        cat_rate = (
                            (stats["passed"] / stats["total"] * 100)
                            if stats["total"] > 0
                            else 0
                        )
                        avg_cat_duration = (
                            stats["duration"] / stats["total"]
                            if stats["total"] > 0
                            else 0
                        )
                        avg_cat_steps = (
                            stats["steps"] / stats["total"] if stats["total"] > 0 else 0
                        )
                        csvfile.write(
                            f"{cat:<30} {cat_rate:>10.1f}% {avg_cat_duration:>12.2f}s {avg_cat_steps:>8.1f}\n"
                        )
                        displayed_cats.add(cat)

                # Display any categories not in the predefined order
                for cat, stats in sorted(category_stats.items()):
                    if cat not in displayed_cats:
                        cat_rate = (
                            (stats["passed"] / stats["total"] * 100)
                            if stats["total"] > 0
                            else 0
                        )
                        avg_cat_duration = (
                            stats["duration"] / stats["total"]
                            if stats["total"] > 0
                            else 0
                        )
                        avg_cat_steps = (
                            stats["steps"] / stats["total"] if stats["total"] > 0 else 0
                        )
                        csvfile.write(
                            f"{cat:<30} {cat_rate:>10.1f}% {avg_cat_duration:>12.2f}s {avg_cat_steps:>8.1f}\n"
                        )

                # Risk Assessment
                csvfile.write("\n")
                csvfile.write("RISK ASSESSMENT\n")
                csvfile.write("-" * 40 + "\n")

                # Identify high-risk categories (< 80% success rate)
                high_risk = [
                    cat
                    for cat, stats in category_stats.items()
                    if (stats["passed"] / stats["total"] * 100) < 80
                    and stats["total"] > 0
                ]

                if high_risk:
                    csvfile.write("High Risk Categories (< 80% success rate):\n")
                    for cat in high_risk:
                        stats = category_stats[cat]
                        rate = stats["passed"] / stats["total"] * 100
                        csvfile.write(f"  - {cat}: {rate:.1f}% success rate\n")
                else:
                    csvfile.write(
                        "All categories performing above 80% success rate threshold.\n"
                    )

                # Performance concerns
                csvfile.write("\n")
                slow_tests = [r for r in self.test_results if r.get("duration", 0) > 30]
                if slow_tests:
                    csvfile.write(
                        f"Performance Concerns: {len(slow_tests)} tests took > 30 seconds\n"
                    )

                # Recommendations
                csvfile.write("\n")
                csvfile.write("RECOMMENDATIONS\n")
                csvfile.write("-" * 40 + "\n")

                if success_rate >= 95:
                    csvfile.write(
                        "✅ Excellent performance - Agent is production ready\n"
                    )
                elif success_rate >= 80:
                    csvfile.write(
                        "⚠️ Good performance - Minor improvements recommended\n"
                    )
                else:
                    csvfile.write(
                        "❌ Poor performance - Significant improvements required\n"
                    )

                if total_errors > total_tests * 0.2:
                    csvfile.write(
                        "⚠️ High error rate detected - Review error handling logic\n"
                    )

                if avg_steps > 5:
                    csvfile.write(
                        "⚠️ High average step count - Consider optimizing agent prompts\n"
                    )

                print(f"\n📊 Executive summary added to: {self.csv_output}")

        except Exception as e:
            print(f"❌ Failed to write final summary: {e}")

    def export_csv_results(self, filename: str = None):
        """Legacy method for compatibility - now just writes final summary."""
        if self.csv_output and self.csv_file_initialized:
            self._write_final_summary()
            return self.csv_output
        return None

    async def run_all_tests(self) -> bool:
        """Run all integration tests."""
        print("🚀 Starting Jira Integration Tests")
        print("=" * 60)
        self.test_start_time = datetime.datetime.now()

        if not await self.setup():
            return False

        test_methods = [
            # Core functionality tests
            self.test_agent_initialization,
            self.test_search_my_issues,
            self.test_time_based_query,
            self.test_issue_type_query,
            self.test_complex_multi_criteria_query,
            self.test_issue_creation,
            self.test_completion_no_timeout,
            self.test_app_methods,
            # Basic demo coverage
            self.test_basic_fetch_queries,
            self.test_advanced_creation_patterns,
            # Intermediate demo coverage
            self.test_intermediate_search_queries,
            self.test_status_priority_queries,
            self.test_advanced_jql_queries,
            # Advanced demo coverage
            self.test_time_based_analysis,
            self.test_complex_filters,
            self.test_multi_step_workflows,
            # Expert demo coverage
            self.test_cross_project_analysis,
            self.test_analytics_queries,
            self.test_bulk_operations,
            # Master demo coverage
            self.test_complex_business_logic,
            self.test_advanced_automation_scenarios,
            self.test_predictive_analysis_queries,
            # Comprehensive demo coverage
            self.test_missing_advanced_queries,
            self.test_strategic_insights_queries,
        ]

        passed = 0
        failed = 0

        for test_method in test_methods:
            try:
                success = await test_method()
                if success:
                    passed += 1
                else:
                    failed += 1

                # Wait for user input in step mode
                if not self.wait_for_user(test_method.__name__):
                    print("\n⚠️  Tests stopped by user")
                    break

            except Exception as e:
                print(f"   ❌ EXCEPTION in {test_method.__name__}: {e}")
                failed += 1

                # Also wait after exceptions in step mode
                if not self.wait_for_user(test_method.__name__):
                    print("\n⚠️  Tests stopped by user")
                    break

        await self.teardown()
        self.test_end_time = datetime.datetime.now()
        test_duration = (self.test_end_time - self.test_start_time).total_seconds()

        # Write final summary to CSV if enabled
        if self.csv_output and self.csv_file_initialized:
            self._write_final_summary()

        # Print summary
        print(f"\n{'='*60}")
        print("📊 TEST SUMMARY")
        print(f"{'='*60}")
        print(f"✅ Passed: {passed}")
        print(f"❌ Failed: {failed}")
        print(
            f"📈 Success Rate: {(passed / (passed + failed) * 100):.1f}%"
            if (passed + failed) > 0
            else "N/A"
        )
        print(f"⏱️  Total Duration: {test_duration:.1f} seconds")

        # If there are failures, show how to rerun them
        failed_tests = [r for r in self.test_results if not r["success"]]
        if failed_tests:
            print(f"\n{'='*60}")
            print("🔧 TO RERUN FAILED TESTS:")
            print(f"{'='*60}")
            # Get unique test methods
            failed_methods = set()
            catalog = self.get_test_catalog()
            for result in failed_tests:
                for category, tests in catalog:
                    for method_name, description in tests:
                        if (
                            description == result["name"]
                            or result["name"] == method_name
                        ):
                            failed_methods.add(method_name)
                            break

            for method in sorted(failed_methods):
                print(f"  python tests/test_jira.py --test {method}")

        # Detailed results
        print(f"\n📋 DETAILED RESULTS:")
        for result in self.test_results:
            status = "✅" if result["success"] else "❌"
            duration_str = (
                f" ({result.get('duration', 0):.1f}s)" if "duration" in result else ""
            )
            # Get the actual test method name from the catalog
            test_method = None
            catalog = self.get_test_catalog()
            for category, tests in catalog:
                for method_name, description in tests:
                    if description == result["name"] or result["name"] == method_name:
                        test_method = method_name
                        break
                if test_method:
                    break

            # Display both human-readable name and test method
            if test_method and test_method != result["name"]:
                print(f"  {status} {result['name']}{duration_str}")
                if not result["success"]:
                    print(f"     Test Method: {test_method}")
                    if result["details"]:
                        print(f"     Details: {result['details']}")
            else:
                print(f"  {status} {result['name']}{duration_str}")
                if not result["success"] and result["details"]:
                    print(f"     Details: {result['details']}")

        return failed == 0


async def main():
    """Main test runner function."""
    import argparse

    parser = argparse.ArgumentParser(description="GAIA Jira Agent Integration Tests")
    parser.add_argument(
        "--step",
        action="store_true",
        help="Enable step mode - pause after each test for inspection",
    )
    parser.add_argument(
        "--test", help="Run a specific test by name (e.g., test_search_my_issues)"
    )
    parser.add_argument(
        "--interactive",
        "-i",
        action="store_true",
        help="Interactive mode - select which tests to run from a menu",
    )
    parser.add_argument(
        "--list", "-l", action="store_true", help="List all available tests and exit"
    )
    parser.add_argument(
        "--csv",
        nargs="?",
        const=True,
        default=None,
        help="Export test results to CSV file (optionally specify filename)",
    )
    parser.add_argument(
        "--model",
        "-m",
        help="Specify LLM model to use (e.g., Qwen3-Coder-30B-A3B-Instruct-GGUF)",
    )
    parser.add_argument(
        "--debug",
        "-d",
        action="store_true",
        help="Enable debug output for troubleshooting",
    )
    parser.add_argument(
        "--show-prompts",
        action="store_true",
        help="Display prompts sent to LLM (separate from debug mode)",
    )
    parser.add_argument(
        "--use-claude",
        action="store_true",
        help="Use Claude API (requires ANTHROPIC_API_KEY)",
    )
    parser.add_argument(
        "--use-chatgpt",
        action="store_true",
        help="Use ChatGPT/OpenAI API (requires OPENAI_API_KEY)",
    )
    args = parser.parse_args()

    # Determine which LLM provider to use
    # Default to local if no provider is specified
    if args.use_claude and args.use_chatgpt:
        # Both remote providers specified - error
        print("❌ Error: Cannot specify both --use-claude and --use-chatgpt")
        print("   Please specify only one LLM provider.")
        return 1

    # Set use_local based on whether a remote provider was selected
    args.use_local = not (args.use_claude or args.use_chatgpt)

    print("🧪 GAIA Jira Agent - Integration Test Suite")
    print("=" * 60)
    print("⚠️  This test suite makes REAL API calls to Jira")
    print("🔑 Requires: ATLASSIAN_SITE_URL, ATLASSIAN_API_KEY, ATLASSIAN_USER_EMAIL")
    if args.step:
        print("🚶 STEP MODE ENABLED - Will pause after each test")
    if args.model:
        print(f"🤖 Using model: {args.model}")
    if args.csv:
        csv_file = args.csv if isinstance(args.csv, str) else "test_results.csv"
        print(f"📊 CSV export enabled: {csv_file}")
    if args.debug:
        print("🐛 DEBUG MODE ENABLED - Will show detailed debug output")
    if args.show_prompts:
        print("📝 PROMPT DISPLAY ENABLED - Will show prompts sent to LLM")
    if args.use_claude:
        print("🤖 CLAUDE API ENABLED - Using model: claude-sonnet-4-20250514")
        print("🔑 Requires: ANTHROPIC_API_KEY environment variable")
    elif args.use_chatgpt:
        print("🤖 OPENAI API ENABLED - Using model: gpt-4o")
        print("🔑 Requires: OPENAI_API_KEY environment variable")
    elif args.use_local:
        print("🏠 LOCAL LLM ENABLED")
    print("=" * 60)

    tests = JiraIntegrationTests(
        step_mode=args.step,
        csv_output=args.csv,
        model=args.model,
        debug=args.debug,
        show_prompts=args.show_prompts,
        use_claude=args.use_claude,
        use_chatgpt=args.use_chatgpt,
    )

    # If --list flag, just show available tests
    if args.list:
        test_map = tests.display_test_menu()
        return 0

    # Interactive mode - let user select tests
    if args.interactive:
        if not await tests.setup():
            return 1

        while True:
            test_map = tests.display_test_menu()

            print(
                "\n📝 Enter test number(s) to run (comma-separated), 0 for all, or 'q' to quit:"
            )
            selection = input("Selection: ").strip().lower()

            if selection == "q":
                print("👋 Exiting...")
                break
            elif selection == "0":
                # Run all tests
                success = await tests.run_all_tests()
                if not success:
                    print("\n⚠️  Some tests failed!")
            else:
                # Parse comma-separated test numbers
                test_nums = [s.strip() for s in selection.split(",")]
                test_names = []

                for num in test_nums:
                    if num in test_map:
                        test_names.append(test_map[num])
                    else:
                        print(f"⚠️  Invalid test number: {num}")

                if test_names:
                    success = await tests.run_selected_tests(test_names)
                    if not success:
                        print("\n⚠️  Some tests failed!")

            print("\n" + "=" * 60)
            print("Would you like to run more tests? (y/n)")
            if input().strip().lower() != "y":
                break

        await tests.teardown()
        return 0

    # If specific test requested, run only that test
    elif args.test:
        if not await tests.setup():
            return 1

        # Find and run the specific test
        test_method = getattr(tests, args.test, None)
        if test_method and callable(test_method):
            print(f"\n🎯 Running specific test: {args.test}")
            try:
                success = await test_method()
                if success:
                    print(f"✅ Test {args.test} passed!")
                else:
                    print(f"❌ Test {args.test} failed!")
            except Exception as e:
                print(f"❌ EXCEPTION in {args.test}: {e}")
                success = False
        else:
            print(f"❌ Test '{args.test}' not found!")
            print("\nAvailable tests:")
            for attr_name in dir(tests):
                if attr_name.startswith("test_"):
                    print(f"  - {attr_name}")
            success = False

        await tests.teardown()
        return 0 if success else 1
    else:
        # Run all tests
        success = await tests.run_all_tests()

        if success:
            print("\n🎉 All tests passed!")
            return 0
        else:
            print("\n⚠️  Some tests failed!")
            return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
