#!/usr/bin/env python
# Copyright(C) 2024-2025 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
JQL Template System for GAIA Orchestrator Agent.
Provides templates and patterns for generating valid JQL queries from natural language.

Reference: https://support.atlassian.com/jira-service-management-cloud/docs/what-is-advanced-search-in-jira-cloud/
"""

# JQL Templates for common patterns
JQL_TEMPLATES = {
    # Issue Types (always quote values)
    "bug": 'issuetype = "Bug"',
    "bugs": 'issuetype = "Bug"',
    "story": 'issuetype = "Story"',
    "stories": 'issuetype = "Story"',
    "task": 'issuetype = "Task"',
    "tasks": 'issuetype = "Task"',
    "epic": 'issuetype = "Epic"',
    "epics": 'issuetype = "Epic"',
    "sub-task": 'issuetype = "Sub-task"',
    "subtask": 'issuetype = "Sub-task"',
    # Priority Levels (always quote values)
    "blocker": 'priority = "Blocker"',
    "critical": 'priority = "Critical"',
    "high priority": 'priority = "High"',
    "high-priority": 'priority = "High"',
    "medium priority": 'priority = "Medium"',
    "low priority": 'priority = "Low"',
    "urgent": 'priority in ("Blocker", "Critical")',
    # Status Values (always quote values)
    "open": 'resolution = "Unresolved"',
    "unresolved": 'resolution = "Unresolved"',
    "resolved": 'resolution != "Unresolved"',
    "closed": 'status = "Closed"',
    "done": 'status = "Done"',
    "in progress": 'status = "In Progress"',
    "in-progress": 'status = "In Progress"',
    "todo": 'status = "To Do"',
    "to do": 'status = "To Do"',
    "blocked": 'status = "Blocked"',
    # Assignment Patterns (using functions)
    "my issues": "assignee = currentUser() OR reporter = currentUser()",
    "assigned to me": "assignee = currentUser()",
    "unassigned": "assignee is EMPTY",
    "no assignee": "assignee is EMPTY",
    "reported by me": "reporter = currentUser()",
    "i reported": "reporter = currentUser()",
    "watching": "watcher = currentUser()",
    "i'm watching": "watcher = currentUser()",
    # Time Functions (proper date functions)
    "created today": "created >= startOfDay()",
    "today's": "created >= startOfDay()",
    "created yesterday": "created >= startOfDay(-1d) AND created < startOfDay()",
    "created this week": "created >= startOfWeek()",
    "this week": "created >= startOfWeek()",
    "created last week": "created >= startOfWeek(-1w) AND created < startOfWeek()",
    "last week": "created >= startOfWeek(-1w) AND created < startOfWeek()",
    "created this month": "created >= startOfMonth()",
    "this month": "created >= startOfMonth()",
    "created last month": "created >= startOfMonth(-1M) AND created < startOfMonth()",
    "last month": "created >= startOfMonth(-1M) AND created < startOfMonth()",
    "last 24 hours": "created >= -24h",
    "last 7 days": "created >= -7d",
    "past week": "created >= -7d",
    "last 30 days": "created >= -30d",
    "past month": "created >= -30d",
    "last 90 days": "created >= -90d",
    "last quarter": "created >= -90d",
    # Updated Time Patterns
    "updated today": "updated >= startOfDay()",
    "recently updated": "updated >= -7d",
    "recent changes": "updated >= -7d",
    "updated this week": "updated >= startOfWeek()",
    "stale": "updated < -30d",
    "not recently updated": "updated < -30d",
    # Sprint Patterns (Agile)
    "current sprint": "sprint in openSprints()",
    "active sprint": "sprint in openSprints()",
    "last sprint": "sprint in closedSprints()",
    "previous sprint": "sprint in closedSprints()",
    "future sprint": "sprint in futureSprints()",
    "no sprint": "sprint is EMPTY",
    "backlog": "sprint is EMPTY",
    # Due Date Patterns
    "overdue": 'duedate < now() AND resolution = "Unresolved"',
    "past due": 'duedate < now() AND resolution = "Unresolved"',
    "due today": "duedate = startOfDay()",
    "due this week": "duedate >= startOfWeek() AND duedate <= endOfWeek()",
    "due soon": "duedate >= now() AND duedate <= 7d",
    "no due date": "duedate is EMPTY",
    # Fix Version Patterns
    "no fix version": "fixVersion is EMPTY",
    "unscheduled": "fixVersion is EMPTY",
    "released": "fixVersion in releasedVersions()",
    "unreleased": "fixVersion in unreleasedVersions()",
    # Component Patterns
    "no component": "component is EMPTY",
    # Epic Link Patterns
    "no epic": '"Epic Link" is EMPTY',
    # Comments and Attachments
    "has comments": 'comment ~ "*"',
    "with comments": 'comment ~ "*"',
    "no comments": "comment is EMPTY",
    "has attachments": "attachments is not EMPTY",
    "with attachments": "attachments is not EMPTY",
    "no attachments": "attachments is EMPTY",
    # Story Points (Agile)
    "no story points": '"Story Points" is EMPTY',
    "unestimated": '"Story Points" is EMPTY',
    # Common explicit queries
    "all issues": "created >= -90d",
    "show all": "created >= -90d",
    "show me all": "created >= -90d",
    "list all": "created >= -90d",
    "everything": "created >= -90d",
}

# Complex patterns that need regex matching
REGEX_PATTERNS = [
    # Project patterns
    (r"project\s+([A-Z0-9]+)", lambda m: f"project = {m.group(1).upper()}"),
    (r"in\s+([A-Z0-9]+)\s+project", lambda m: f"project = {m.group(1).upper()}"),
    # Story points comparisons
    (
        r"story points?\s*(?:>|greater than)\s*(\d+)",
        lambda m: f'"Story Points" > {m.group(1)}',
    ),
    (
        r"story points?\s*(?:<|less than)\s*(\d+)",
        lambda m: f'"Story Points" < {m.group(1)}',
    ),
    (
        r"story points?\s*(?:>=|at least)\s*(\d+)",
        lambda m: f'"Story Points" >= {m.group(1)}',
    ),
    (
        r"story points?\s*(?:<=|at most)\s*(\d+)",
        lambda m: f'"Story Points" <= {m.group(1)}',
    ),
    (
        r"story points?\s*(?:=|equals?|is)\s*(\d+)",
        lambda m: f'"Story Points" = {m.group(1)}',
    ),
    # Time comparisons for custom fields
    (
        r"created\s+(?:after|since)\s+(\d{4}-\d{2}-\d{2})",
        lambda m: f'created >= "{m.group(1)}"',
    ),
    (r"created\s+before\s+(\d{4}-\d{2}-\d{2})", lambda m: f'created < "{m.group(1)}"'),
    (
        r"updated\s+(?:after|since)\s+(\d{4}-\d{2}-\d{2})",
        lambda m: f'updated >= "{m.group(1)}"',
    ),
    (r"updated\s+before\s+(\d{4}-\d{2}-\d{2})", lambda m: f'updated < "{m.group(1)}"'),
    # Assignee patterns
    (
        r"assigned to\s+([a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+)",
        lambda m: f'assignee = "{m.group(1)}"',
    ),
    (r'assigned to\s+"([^"]+)"', lambda m: f'assignee = "{m.group(1)}"'),
    # Exact phrase search
    (r'"([^"]+)"', lambda m: f'text ~ "{m.group(1)}"'),
]

# Label mappings
LABEL_MAPPINGS = {
    "security": ["security", "vulnerability", "sec"],
    "performance": ["performance", "perf", "slow"],
    "ui": ["ui", "ux", "frontend"],
    "ux": ["ui", "ux", "frontend"],
    "backend": ["backend", "api", "server"],
    "api": ["backend", "api", "server"],
    "database": ["database", "db", "sql"],
    "testing": ["testing", "test", "qa"],
    "documentation": ["documentation", "docs", "doc"],
}

# Team mappings
TEAM_PATTERNS = {
    "backend team": 'assignee in membersOf("backend-team")',
    "frontend team": 'assignee in membersOf("frontend-team")',
    "qa team": 'assignee in membersOf("qa-team")',
    "test team": 'assignee in membersOf("qa-team")',
    "security team": 'assignee in membersOf("security-team")',
    "devops team": 'assignee in membersOf("devops-team")',
    "my team": 'assignee in membersOf("dev-team")',
}

# Composite patterns (combine multiple conditions)
COMPOSITE_PATTERNS = {
    "critical bugs": 'priority = "Critical" AND issuetype = "Bug"',
    "high priority bugs": 'priority = "High" AND issuetype = "Bug"',
    "open bugs": 'issuetype = "Bug" AND resolution = "Unresolved"',
    "my open issues": 'assignee = currentUser() AND resolution = "Unresolved"',
    "overdue tasks": 'issuetype = "Task" AND duedate < now() AND resolution = "Unresolved"',
    "unassigned bugs": 'issuetype = "Bug" AND assignee is EMPTY',
    "blocked stories": 'issuetype = "Story" AND status = "Blocked"',
}

# Order by patterns
ORDER_PATTERNS = {
    "recent": "ORDER BY created DESC",
    "recently created": "ORDER BY created DESC",
    "latest": "ORDER BY created DESC",
    "newest": "ORDER BY created DESC",
    "oldest": "ORDER BY created ASC",
    "urgent": "ORDER BY priority DESC, created DESC",
    "by priority": "ORDER BY priority DESC",
    "priority": "ORDER BY priority DESC",
    "due date": "ORDER BY duedate ASC",
    "by due date": "ORDER BY duedate ASC",
    "updated": "ORDER BY updated DESC",
    "recently updated": "ORDER BY updated DESC",
}


def generate_jql_from_templates(query: str) -> str:
    """
    Generate JQL query from natural language using templates.

    Args:
        query: Natural language query

    Returns:
        Valid JQL query string
    """
    import re

    query_lower = query.lower()
    jql_parts = []
    used_templates = set()

    # Check for exact phrase matches first (most specific)
    for pattern, jql in JQL_TEMPLATES.items():
        if pattern in query_lower and pattern not in used_templates:
            jql_parts.append(jql)
            used_templates.add(pattern)
            # Mark overlapping patterns as used
            break  # Use only the first match to avoid duplicates

    # If no template matched, check composite patterns
    if not jql_parts:
        for pattern, jql in COMPOSITE_PATTERNS.items():
            if pattern in query_lower:
                jql_parts.append(jql)
                break

    # Apply regex patterns
    for pattern, generator in REGEX_PATTERNS:
        match = re.search(pattern, query, re.IGNORECASE)
        if match:
            jql_part = generator(match)
            # Avoid duplicates
            if jql_part not in jql_parts:
                jql_parts.append(jql_part)

    # Handle labels
    labels_to_add = set()
    for keyword, labels in LABEL_MAPPINGS.items():
        if keyword in query_lower:
            labels_to_add.update(labels)

    if labels_to_add:
        labels_str = ", ".join(f'"{label}"' for label in labels_to_add)
        jql_parts.append(f"labels in ({labels_str})")

    # Handle teams
    for team_pattern, jql in TEAM_PATTERNS.items():
        if team_pattern in query_lower:
            jql_parts.append(jql)
            break

    # Build the final JQL - no fallbacks, explicit mapping only
    if jql_parts:
        # Combine with AND by default
        if " or " in query_lower:
            jql = " OR ".join(jql_parts)
        else:
            jql = " AND ".join(jql_parts)
    else:
        # If no templates match, return a simple default query
        jql = "created >= -30d"

    # Add ordering
    order_clause = ""
    for order_pattern, order_jql in ORDER_PATTERNS.items():
        if order_pattern in query_lower:
            order_clause = f" {order_jql}"
            break

    # Default ordering if none specified
    if not order_clause:
        order_clause = " ORDER BY updated DESC"

    jql += order_clause

    return jql
