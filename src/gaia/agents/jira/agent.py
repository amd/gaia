#!/usr/bin/env python
# Copyright(C) 2024-2025 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Jira Agent for GAIA.

This agent provides an intelligent natural language interface to Jira with automatic
configuration discovery. It can adapt to any Jira instance by discovering available
projects, issue types, statuses, and priorities dynamically.

Key Features:
- Automatic Jira instance discovery and configuration
- Natural language to JQL query translation
- Robust error handling for invalid configurations
- Portable across different Jira instances
- JSON API support for web application integration
"""

import asyncio
import base64
import logging
import os
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

import aiohttp

from gaia.agents.base.agent import Agent
from gaia.agents.base.console import AgentConsole, SilentConsole
from gaia.agents.base.tools import tool

logger = logging.getLogger(__name__)


@dataclass
class JiraIssue:
    """Represents a Jira issue."""

    key: str
    summary: str
    status: str
    priority: str
    issue_type: str
    assignee: str


@dataclass
class JiraSearchResult:
    """Represents search results from Jira."""

    issues: List[JiraIssue]
    total: int
    jql: str


class JiraAgent(Agent):
    """
    Intelligent Jira agent with automatic configuration discovery.

    This agent provides a natural language interface to Jira that automatically
    adapts to any Jira instance by discovering its configuration (projects,
    issue types, statuses, priorities) and generating appropriate JQL queries.

    Capabilities:
    - Search for issues using natural language or JQL
    - Create new issues with intelligent parameter detection
    - Update existing issues
    - Generate proper JQL queries from natural language
    - Automatic Jira instance configuration discovery
    - Robust error handling and validation

    Usage:
        # Basic usage with discovery
        agent = JiraAgent()
        config = agent.initialize()  # Discovers Jira configuration
        result = agent.process_query("Show me my high priority issues")

        # Advanced usage with pre-configured setup
        agent = JiraAgent(jira_config=pre_discovered_config)
        result = agent.process_query("Create a new idea called 'AI Integration'")

    Environment Variables Required:
        ATLASSIAN_SITE_URL: Your Jira site URL (e.g., https://company.atlassian.net)
        ATLASSIAN_API_KEY: Your Jira API token
        ATLASSIAN_USER_EMAIL: Your Jira account email
    """

    def __init__(self, jira_config: Dict[str, Any] = None, **kwargs):
        """Initialize the Jira agent.

        Args:
            jira_config: Optional pre-discovered Jira configuration containing:
                - projects: List of {'key': str, 'name': str} project dictionaries
                - issue_types: List of available issue type names
                - statuses: List of available status names
                - priorities: List of available priority names
            **kwargs: Other agent initialization parameters:
                - max_steps: Maximum conversation steps (default: 10)
                - model_id: LLM model to use (default: Qwen3-Coder-30B-A3B-Instruct-GGUF)
                - silent_mode: Suppress console output (default: False)
                - debug: Enable debug logging (default: False)
                - show_prompts: Display prompts sent to LLM (default: False)

        Example:
            # Without config (will use defaults until initialize() is called)
            agent = JiraAgent(silent_mode=True)

            # With pre-discovered config
            config = {
                "projects": [{"key": "PROJ", "name": "My Project"}],
                "issue_types": ["Bug", "Task", "Story"],
                "statuses": ["To Do", "In Progress", "Done"],
                "priorities": ["High", "Medium", "Low"]
            }
            agent = JiraAgent(jira_config=config)
        """
        # Increase max steps to allow for completion
        if "max_steps" not in kwargs:
            kwargs["max_steps"] = 10
        # Use the larger coding model by default for reliable JSON parsing
        if "model_id" not in kwargs:
            kwargs["model_id"] = "Qwen3-Coder-30B-A3B-Instruct-GGUF"

        # Store config before calling super() so system prompt can use it
        self._jira_config = jira_config
        self._jira_credentials = None

        super().__init__(**kwargs)

    def _get_system_prompt(self) -> str:
        """Generate the system prompt for the Jira agent.

        Creates a dynamic system prompt that adapts to the discovered Jira configuration.
        If configuration has been discovered via initialize(), it will include:
        - Actual project keys available in the instance
        - Real issue types, priorities, and statuses
        - Proper JQL syntax guidance with instance-specific values

        Returns:
            str: System prompt tailored to the Jira instance configuration
        """
        # Try to get discovered configuration for dynamic prompt
        jira_config = None
        if hasattr(self, "_jira_config") and self._jira_config:
            jira_config = self._jira_config

        # Base prompt - strict JSON-only output for better compatibility
        prompt = """You are a Jira assistant that responds ONLY in JSON format.

**CRITICAL RULES:**
1. Output ONLY valid JSON - nothing else
2. Do NOT add any text before the opening {
3. Do NOT add any text after the closing }
4. Your ENTIRE response must be parseable JSON

Capabilities:
- Search issues (JQL queries)
- Create issues
- Update issues

JQL basics:
- Current user: assignee = currentUser()
- By key: key = "PROJ-123"
- Operators: AND, OR, NOT"""

        # Add discovered configuration if available
        if jira_config:
            if jira_config.get("projects"):
                project_keys = [p["key"] for p in jira_config["projects"]]
                prompt += (
                    f"\n- Projects: Available projects are {', '.join(project_keys)}"
                )

            if jira_config.get("issue_types"):
                types = jira_config["issue_types"]
                prompt += f"\n- Types: Available issue types are {', '.join(types)}"

            if jira_config.get("priorities"):
                priorities = jira_config["priorities"]
                prompt += (
                    f"\n- Priority: Available priorities are {', '.join(priorities)}"
                )

            if jira_config.get("statuses"):
                statuses = jira_config["statuses"]
                prompt += f"\n- Status: Available statuses are {', '.join(statuses)}"
        else:
            # Fallback to default values if discovery hasn't run yet
            prompt += """
- Types: issuetype = "Idea" (Note: This Jira instance uses "Idea" not "Bug"/"Task"/"Story")
- Priority: priority = "Critical", priority = "High", priority = "Medium"
- Status: status = "Parking lot", status = "In Progress", status = "Done"
"""

        prompt += """
- Time: created >= -7d, created >= startOfWeek()

JQL QUOTING RULES (CRITICAL):
- Single words: NO quotes → status = Done, issuetype = Story
- Multiple words: DOUBLE quotes → status = "In Progress", summary ~ "user story"  
- NEVER use single quotes ('') in JQL
- Functions: NO quotes → assignee = currentUser(), sprint in openSprints()

RESPONSE FORMAT - Use EXACTLY this structure:

For SEARCH (most common):
{"thought": "User wants X, I'll search for Y", "goal": "Search Jira", "plan": [{"tool": "jira_search", "tool_args": {"jql": "YOUR_JQL_HERE"}}]}

For CREATE:
{"thought": "User wants to create X", "goal": "Create issue", "plan": [{"tool": "jira_create", "tool_args": {"summary": "Title", "description": "Details", "issue_type": "Task"}}]}

For FINAL ANSWER (after tool execution):
{"thought": "Search completed", "goal": "Report results", "answer": "Found X issues: [specific details]"}

EXAMPLES - Copy these patterns exactly:

User: "show my issues"
{"thought": "User wants their assigned issues", "goal": "Search Jira", "plan": [{"tool": "jira_search", "tool_args": {"jql": "assignee = currentUser()"}}]}

User: "high priority bugs"  
{"thought": "User wants high priority bugs", "goal": "Search Jira", "plan": [{"tool": "jira_search", "tool_args": {"jql": "priority = High AND issuetype = Bug"}}]}

User: "stories in progress"
{"thought": "User wants in progress stories", "goal": "Search Jira", "plan": [{"tool": "jira_search", "tool_args": {"jql": "status = \\"In Progress\\" AND issuetype = Story"}}]}

User: "issues created this week"
{"thought": "User wants recent issues", "goal": "Search Jira", "plan": [{"tool": "jira_search", "tool_args": {"jql": "created >= startOfWeek()"}}]}

User: "current sprint stories"
{"thought": "User wants stories in current sprint", "goal": "Search Jira", "plan": [{"tool": "jira_search", "tool_args": {"jql": "issuetype = Story AND sprint in openSprints()"}}]}

RULES:
1. Response MUST be valid JSON only
2. Use double quotes for ALL JSON strings
3. In JQL: NO quotes for single words, DOUBLE quotes for phrases
4. Escape quotes in JQL with backslash: \\"In Progress\\"
5. Copy the exact format from examples"""

        return prompt

    def _create_console(self):
        """Create console for Jira agent output.

        Returns:
            AgentConsole or SilentConsole: Console instance based on silent_mode setting
        """
        if self.silent_mode:
            return SilentConsole()
        return AgentConsole()

    def initialize(self) -> Dict[str, Any]:
        """
        Discover and cache Jira instance configuration.

        This method connects to your Jira instance and discovers:
        - Available projects (keys and names)
        - Available issue types (excluding subtasks)
        - Available statuses
        - Available priorities

        The discovered configuration is cached and used to generate dynamic
        system prompts that help the LLM understand your specific Jira setup.

        Returns:
            Dictionary containing discovered Jira configuration:
            {
                "projects": [{"key": "PROJ", "name": "Project Name"}, ...],
                "issue_types": ["Bug", "Task", "Story", ...],
                "statuses": ["To Do", "In Progress", "Done", ...],
                "priorities": ["Highest", "High", "Medium", "Low", "Lowest"]
            }

        Raises:
            Exception: If Jira credentials are invalid or API calls fail

        Example:
            agent = JiraAgent()
            config = agent.initialize()
            print(f"Found {len(config['projects'])} projects")

        Note:
            This method should be called once after creating the agent and before
            making queries. The configuration is cached for subsequent use.
        """
        try:
            import concurrent.futures

            with concurrent.futures.ThreadPoolExecutor() as executor:
                future = executor.submit(asyncio.run, self._discover_jira_config())
                self._jira_config = future.result(timeout=10)

            logger.info(
                f"Jira configuration discovered: {len(self._jira_config.get('projects', []))} projects, "
                f"{len(self._jira_config.get('issue_types', []))} issue types, "
                f"{len(self._jira_config.get('statuses', []))} statuses"
            )

            return self._jira_config

        except Exception as e:
            logger.error(f"Jira discovery failed: {e}")
            # Set empty config so we don't retry
            self._jira_config = {
                "projects": [],
                "issue_types": [],
                "statuses": [],
                "priorities": [],
            }
            return self._jira_config

    def get_config(self) -> Optional[Dict[str, Any]]:
        """Get the current Jira configuration.

        Returns:
            Optional[Dict[str, Any]]: The cached Jira configuration if discovered,
                                     None if initialize() hasn't been called yet
        """
        return self._jira_config

    def _register_tools(self):
        """Register Jira-specific tools.

        Registers three main tools for Jira interaction:
        - jira_search: Search issues using JQL queries
        - jira_create: Create new issues with specified fields
        - jira_update: Update existing issues by key

        These tools are exposed to the LLM for natural language processing
        and are automatically invoked based on user intent.
        """

        @tool
        def jira_search(
            jql: str = "created >= -30d ORDER BY updated DESC",
            max_results: int = None,
            fields: str = None,
        ) -> Dict[str, Any]:
            """Search Jira issues using JQL query.

            Args:
                jql: JQL query string for searching issues
                max_results: Maximum number of results to return (default: 50)
                fields: Comma-separated list of fields to return (optional)

            Returns:
                Dictionary containing search results
            """
            return self._execute_jira_search(jql, max_results, fields)

        @tool
        def jira_create(
            summary: str,
            description: str = "",
            issue_type: str = "Task",
            priority: str = None,
            project: str = None,
        ) -> Dict[str, Any]:
            """Create a new Jira issue.

            Args:
                summary: Issue title/summary
                description: Detailed description of the issue
                issue_type: Type of issue (Bug, Task, Story, etc.)
                priority: Priority level (Critical, High, Medium, Low)
                project: Project key (optional)

            Returns:
                Dictionary containing created issue details
            """
            return self._execute_jira_create(
                summary, description, issue_type, priority, project
            )

        @tool
        def jira_update(
            issue_key: str,
            summary: str = None,
            description: str = None,
            priority: str = None,
            status: str = None,
        ) -> Dict[str, Any]:
            """Update an existing Jira issue.

            Args:
                issue_key: Key of the issue to update (e.g., PROJ-123)
                summary: New summary/title
                description: New description
                priority: New priority level
                status: New status

            Returns:
                Dictionary containing update result
            """
            return self._execute_jira_update(
                issue_key, summary, description, priority, status
            )

    def _get_jira_credentials(self) -> tuple[str, str, str]:
        """Get Jira credentials from environment.

        Retrieves and validates Jira credentials from environment variables.
        Caches credentials after first retrieval for performance.

        Returns:
            tuple[str, str, str]: Tuple of (site_url, api_key, user_email)

        Raises:
            ValueError: If any required environment variable is missing
        """
        if self._jira_credentials is None:
            site_url = os.getenv("ATLASSIAN_SITE_URL")
            api_key = os.getenv("ATLASSIAN_API_KEY")
            user_email = os.getenv("ATLASSIAN_USER_EMAIL")

            if not all([site_url, api_key, user_email]):
                raise ValueError(
                    "Missing Jira credentials - set ATLASSIAN_SITE_URL, ATLASSIAN_API_KEY, ATLASSIAN_USER_EMAIL"
                )

            # Clean up credentials
            site_url = site_url.rstrip("/")
            if not site_url.startswith(("http://", "https://")):
                site_url = f"https://{site_url}"

            self._jira_credentials = (site_url, api_key, user_email)

        return self._jira_credentials

    async def _discover_jira_config(self) -> Dict[str, Any]:
        """
        Internal method to discover Jira instance configuration via API calls.

        Makes concurrent API calls to discover:
        - /rest/api/3/project - Available projects
        - /rest/api/3/issuetype - Available issue types (excluding subtasks)
        - /rest/api/3/status - Available statuses
        - /rest/api/3/priority - Available priorities

        Returns:
            Dictionary containing discovered configuration

        Note:
            This is an internal async method. Use initialize() instead.
        """
        if self._jira_config is not None:
            return self._jira_config

        logger.debug("Discovering Jira instance configuration...")

        site_url, api_key, user_email = self._get_jira_credentials()
        auth_string = f"{user_email}:{api_key}"
        auth_header = base64.b64encode(auth_string.encode()).decode()

        headers = {
            "Authorization": f"Basic {auth_header}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

        config = {"projects": [], "issue_types": [], "statuses": [], "priorities": []}

        async with aiohttp.ClientSession() as session:
            try:
                # Get projects
                projects_url = f"{site_url}/rest/api/3/project"
                async with session.get(projects_url, headers=headers) as response:
                    if response.status == 200:
                        projects = await response.json()
                        config["projects"] = [
                            {"key": p["key"], "name": p["name"]} for p in projects
                        ]

                # Get issue types
                types_url = f"{site_url}/rest/api/3/issuetype"
                async with session.get(types_url, headers=headers) as response:
                    if response.status == 200:
                        types = await response.json()
                        config["issue_types"] = [
                            t["name"] for t in types if not t.get("subtask", False)
                        ]

                # Get statuses
                statuses_url = f"{site_url}/rest/api/3/status"
                async with session.get(statuses_url, headers=headers) as response:
                    if response.status == 200:
                        statuses = await response.json()
                        config["statuses"] = [s["name"] for s in statuses]

                # Get priorities
                priorities_url = f"{site_url}/rest/api/3/priority"
                async with session.get(priorities_url, headers=headers) as response:
                    if response.status == 200:
                        priorities = await response.json()
                        config["priorities"] = [p["name"] for p in priorities]

            except Exception as e:
                logger.warning(f"Failed to discover some Jira configuration: {e}")

        self._jira_config = config
        logger.debug(f"Discovered Jira config: {config}")
        return config

    def _execute_jira_search(
        self, jql: str, max_results: int = None, fields: str = None
    ) -> Dict[str, Any]:
        """Execute Jira search synchronously.

        Wrapper method that runs the async search in a thread pool executor.

        Args:
            jql: JQL query string for searching issues
            max_results: Maximum number of results to return (default: 50)
            fields: Comma-separated list of fields to return (optional)

        Returns:
            Dict[str, Any]: Search results containing 'issues', 'total', and 'jql' keys,
                          or error dict with 'status' and 'error' keys on failure
        """
        try:
            import concurrent.futures

            with concurrent.futures.ThreadPoolExecutor() as executor:
                future = executor.submit(
                    asyncio.run,
                    self._execute_jira_search_async(jql, max_results, fields),
                )
                return future.result(timeout=30)
        except Exception as e:
            return {"status": "error", "error": f"Async execution failed: {str(e)}"}

    def _execute_jira_create(
        self,
        summary: str,
        description: str,
        issue_type: str,
        priority: str,
        project: str,
    ) -> Dict[str, Any]:
        """Execute Jira issue creation synchronously.

        Wrapper method that runs the async creation in a thread pool executor.

        Args:
            summary: Issue title/summary
            description: Detailed description of the issue
            issue_type: Type of issue (Bug, Task, Story, etc.)
            priority: Priority level (Critical, High, Medium, Low)
            project: Project key (uses first available if not specified)

        Returns:
            Dict[str, Any]: Creation result with 'status', 'created', 'key', 'id', and 'url' keys,
                          or error dict with 'status' and 'error' keys on failure
        """
        try:
            import concurrent.futures

            with concurrent.futures.ThreadPoolExecutor() as executor:
                future = executor.submit(
                    asyncio.run,
                    self._execute_jira_create_async(
                        summary, description, issue_type, priority, project
                    ),
                )
                return future.result(timeout=30)
        except Exception as e:
            return {"status": "error", "error": f"Async execution failed: {str(e)}"}

    def _execute_jira_update(
        self, issue_key: str, summary: str, description: str, priority: str, status: str
    ) -> Dict[str, Any]:
        """Execute Jira issue update synchronously.

        Wrapper method that runs the async update in a thread pool executor.

        Args:
            issue_key: Key of the issue to update (e.g., PROJ-123)
            summary: New summary/title (optional)
            description: New description (optional)
            priority: New priority level (optional)
            status: New status (optional)

        Returns:
            Dict[str, Any]: Update result with 'status', 'updated', 'key', and 'url' keys,
                          or error dict with 'status' and 'error' keys on failure
        """
        try:
            import concurrent.futures

            with concurrent.futures.ThreadPoolExecutor() as executor:
                future = executor.submit(
                    asyncio.run,
                    self._execute_jira_update_async(
                        issue_key, summary, description, priority, status
                    ),
                )
                return future.result(timeout=30)
        except Exception as e:
            return {"status": "error", "error": f"Async execution failed: {str(e)}"}

    async def _execute_jira_search_async(
        self, jql: str, max_results: int = None, fields: str = None
    ) -> Dict[str, Any]:
        """Execute Jira search API call.

        Makes an async HTTP request to Jira's search API endpoint.

        Args:
            jql: JQL query string for searching issues
            max_results: Maximum number of results to return (default: 50)
            fields: Comma-separated list of fields to return (optional)

        Returns:
            Dict[str, Any]: Dictionary containing:
                - issues: List of issue dictionaries with key, summary, status, priority, type, assignee
                - total: Total number of matching issues
                - jql: The JQL query used

        Raises:
            aiohttp.ClientResponseError: If the API request fails
        """
        logger.debug(
            f"Executing Jira search with JQL: {jql}, max_results: {max_results}, fields: {fields}"
        )

        site_url, api_key, user_email = self._get_jira_credentials()

        # Create auth header
        auth_string = f"{user_email}:{api_key}"
        auth_header = base64.b64encode(auth_string.encode()).decode()

        headers = {
            "Authorization": f"Basic {auth_header}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

        async with aiohttp.ClientSession() as session:
            # Using the new Enhanced JQL Service endpoint
            url = f"{site_url}/rest/api/3/search/jql"
            params = {"jql": jql}

            # Add optional parameters
            if max_results is not None:
                params["maxResults"] = max_results

            # Always request the basic fields we need unless specific fields are requested
            if fields is not None:
                params["fields"] = fields
            else:
                params["fields"] = "key,summary,status,priority,issuetype,assignee"

            logger.debug(f"Making API request to: {url}")

            async with session.get(url, headers=headers, params=params) as response:
                response.raise_for_status()
                data = await response.json()

                issues = []
                for issue in data.get("issues", []):
                    fields = issue.get("fields", {})
                    issues.append(
                        {
                            "key": issue.get("key"),
                            "summary": fields.get("summary"),
                            "status": (
                                fields.get("status", {}).get("name")
                                if fields.get("status")
                                else None
                            ),
                            "priority": (
                                fields.get("priority", {}).get("name")
                                if fields.get("priority")
                                else None
                            ),
                            "type": (
                                fields.get("issuetype", {}).get("name")
                                if fields.get("issuetype")
                                else None
                            ),
                            "assignee": (
                                fields.get("assignee", {}).get("displayName")
                                if fields.get("assignee")
                                else "Unassigned"
                            ),
                        }
                    )

                result = {
                    "issues": issues,
                    "total": data.get("total", len(issues)),
                    "jql": jql,
                }

                logger.debug(f"Found {len(issues)} issues")
                return result

    async def _execute_jira_create_async(
        self,
        summary: str,
        description: str = "",
        issue_type: str = "Task",
        priority: str = None,
        project: str = None,
    ) -> Dict[str, Any]:
        """Execute Jira issue creation API call.

        Makes an async HTTP POST request to create a new Jira issue.
        If no project is specified, uses the first available project.

        Args:
            summary: Issue title/summary (required)
            description: Detailed description (defaults to "Created via GAIA")
            issue_type: Type of issue (defaults to "Task")
            priority: Priority level (optional)
            project: Project key (optional, auto-selected if not provided)

        Returns:
            Dict[str, Any]: Dictionary containing:
                - status: "success" or "error"
                - created: True if successful
                - key: Issue key (e.g., PROJ-123)
                - id: Issue ID
                - url: Direct link to the issue
                Or on error:
                - status: "error"
                - error: Error message

        Raises:
            aiohttp.ClientResponseError: If the API request fails
        """
        logger.debug(f"Creating Jira issue: {summary}")

        site_url, api_key, user_email = self._get_jira_credentials()

        # Create auth header
        auth_string = f"{user_email}:{api_key}"
        auth_header = base64.b64encode(auth_string.encode()).decode()

        headers = {
            "Authorization": f"Basic {auth_header}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

        async with aiohttp.ClientSession() as session:
            # Get project if not specified
            if not project:
                projects_url = f"{site_url}/rest/api/3/project"
                async with session.get(projects_url, headers=headers) as resp:
                    resp.raise_for_status()
                    projects = await resp.json()
                    if projects:
                        project = projects[0]["key"]
                    else:
                        return {"status": "error", "error": "No projects available"}

            payload = {
                "fields": {
                    "project": {"key": project},
                    "summary": summary,
                    "description": description or "Created via GAIA",
                    "issuetype": {"name": issue_type},
                }
            }

            if priority:
                payload["fields"]["priority"] = {"name": priority}

            url = f"{site_url}/rest/api/3/issue"
            logger.debug(f"Creating issue with payload: {payload}")

            async with session.post(url, headers=headers, json=payload) as response:
                if response.status == 400:
                    # Parse the error response to provide more context
                    try:
                        error_data = await response.json()
                        error_msg = error_data.get("errorMessages", [])
                        if error_msg:
                            detailed_error = f"Bad Request: {'; '.join(error_msg)}"
                        else:
                            detailed_error = f"Bad Request (400): Invalid issue type '{issue_type}' or other field validation failed"

                        return {
                            "status": "error",
                            "error": f"{detailed_error}. Available issue types for this Jira instance may be different (try 'Idea', 'Bug', 'Story', 'Task', or 'Subtask'). Use jira_search with 'project IS NOT EMPTY' to discover valid issue types.",
                        }
                    except Exception:
                        return {
                            "status": "error",
                            "error": f"Bad Request (400): Issue creation failed. The issue type '{issue_type}' may not be valid for this Jira instance. Try using 'Idea' instead, or check available issue types first.",
                        }

                response.raise_for_status()  # For other HTTP errors
                result = await response.json()

                return {
                    "status": "success",
                    "created": True,
                    "key": result.get("key"),
                    "id": result.get("id"),
                    "url": f"{site_url}/browse/{result.get('key')}",
                    "metadata": {
                        "project": project,
                        "issue_type": issue_type,
                        "priority": priority,
                        "summary": summary,
                    },
                }

    async def _execute_jira_update_async(
        self,
        issue_key: str,
        summary: str = None,
        description: str = None,
        priority: str = None,
        status: str = None,
    ) -> Dict[str, Any]:
        """Execute Jira issue update API call.

        Makes an async HTTP PUT request to update an existing Jira issue.
        Only updates fields that are provided (non-None).

        Args:
            issue_key: Key of the issue to update (e.g., PROJ-123) (required)
            summary: New summary/title (optional)
            description: New description (optional)
            priority: New priority level (optional)
            status: New status (optional)

        Returns:
            Dict[str, Any]: Dictionary containing:
                - status: "success" or "error"
                - updated: True if successful
                - key: Issue key that was updated
                - url: Direct link to the issue
                Or on error:
                - status: "error"
                - error: Error message

        Raises:
            aiohttp.ClientResponseError: If the API request fails
        """
        if not issue_key:
            return {"status": "error", "error": "Issue key is required"}

        logger.debug(f"Updating Jira issue: {issue_key}")

        site_url, api_key, user_email = self._get_jira_credentials()

        # Create auth header
        auth_string = f"{user_email}:{api_key}"
        auth_header = base64.b64encode(auth_string.encode()).decode()

        headers = {
            "Authorization": f"Basic {auth_header}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

        # Build update payload
        fields = {}
        if summary:
            fields["summary"] = summary
        if description:
            fields["description"] = description
        if priority:
            fields["priority"] = {"name": priority}
        if status:
            fields["status"] = {"name": status}

        if not fields:
            return {"status": "error", "error": "No fields to update"}

        payload = {"fields": fields}

        async with aiohttp.ClientSession() as session:
            url = f"{site_url}/rest/api/3/issue/{issue_key}"
            logger.debug(f"Updating issue {issue_key} with payload: {payload}")

            async with session.put(url, headers=headers, json=payload) as response:
                response.raise_for_status()

                return {
                    "status": "success",
                    "updated": True,
                    "key": issue_key,
                    "url": f"{site_url}/browse/{issue_key}",
                    "metadata": {
                        "updated_fields": list(fields.keys()),
                        "summary": summary,
                        "priority": priority,
                        "status": status,
                    },
                }
