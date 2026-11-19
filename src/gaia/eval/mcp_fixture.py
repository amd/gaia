# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Mock MCP servers for the tool-cost and tool-recall harnesses (#1971).

Each mock stands in for a connected ``MCPClient``: it has the ``name``,
``prefix`` and ``list_tools()`` that :meth:`MCPClientMixin._convert_mcp_tools`
reads, so the registry entries a harness measures come out of GAIA's real
conversion code, not a copy of it. Nothing here starts a process or opens a
socket.

The tools are shaped like the ones real servers expose (repository, chat,
filesystem, calendar and issue-tracker servers): typed parameters with their
own descriptions, so their schemas cost what real ones cost. Each tool carries
a ``probe``, a request that needs that tool without spelling out its name
(it may share words with it, as real requests do), for the recall check.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Callable, Dict, List, Sequence, Tuple, cast

from gaia.mcp.client.mcp_client import MCPTool

if TYPE_CHECKING:  # pragma: no cover - typing only
    from gaia.mcp.client.mcp_client import MCPClient


@dataclass(frozen=True)
class MockToolSpec:
    """One tool a mock server lists, and a request that needs it."""

    name: str
    description: str
    params: Tuple[
        Tuple[str, str, str, bool], ...
    ]  # (name, type, description, required)
    probe: str

    def to_mcp_tool(self) -> MCPTool:
        properties: Dict[str, Dict[str, Any]] = {
            name: {"type": kind, "description": desc}
            for name, kind, desc, _ in self.params
        }
        for name, kind, _, _ in self.params:
            if kind == "array":
                properties[name]["items"] = {"type": "string"}
        return MCPTool(
            name=self.name,
            description=self.description,
            input_schema={
                "type": "object",
                "properties": properties,
                "required": [name for name, _, _, req in self.params if req],
            },
        )


def _tool(name, description, params, probe) -> MockToolSpec:
    return MockToolSpec(name, description, tuple(params), probe)


SERVER_KINDS: Dict[str, Tuple[MockToolSpec, ...]] = {
    "repos": (
        _tool(
            "create_issue",
            "Open a new issue in a repository with a title, body and labels.",
            [
                ("repo", "string", "Repository as owner/name.", True),
                ("title", "string", "Issue title.", True),
                ("body", "string", "Issue description in Markdown.", False),
                ("labels", "array", "Labels to apply.", False),
            ],
            "open a new issue in the repository titled login page crashes",
        ),
        _tool(
            "list_pull_requests",
            "List pull requests in a repository, filtered by state and base branch.",
            [
                ("repo", "string", "Repository as owner/name.", True),
                ("state", "string", "open, closed or all.", False),
                ("base", "string", "Only pull requests into this branch.", False),
            ],
            "which pull requests are still open in the repository",
        ),
        _tool(
            "get_file_contents",
            "Read a file from a repository at a branch, tag or commit.",
            [
                ("repo", "string", "Repository as owner/name.", True),
                ("path", "string", "Path of the file in the repository.", True),
                ("ref", "string", "Branch, tag or commit SHA.", False),
            ],
            "read the file contents of setup.py from the repository main branch",
        ),
        _tool(
            "create_branch",
            "Create a branch in a repository from an existing branch.",
            [
                ("repo", "string", "Repository as owner/name.", True),
                ("branch", "string", "Name of the new branch.", True),
                ("from_branch", "string", "Branch to start from.", False),
            ],
            "create a branch called release-2 in the repository",
        ),
        _tool(
            "search_code",
            "Search code across repositories with a query string.",
            [
                ("query", "string", "Search query, with qualifiers.", True),
                ("per_page", "integer", "Results per page, up to 100.", False),
            ],
            "search code across repositories for the parse_config function",
        ),
        _tool(
            "add_issue_comment",
            "Add a comment to an existing issue or pull request.",
            [
                ("repo", "string", "Repository as owner/name.", True),
                ("issue_number", "integer", "Issue or pull request number.", True),
                ("body", "string", "Comment text in Markdown.", True),
            ],
            "add a comment to issue 42 saying the fix is merged",
        ),
        _tool(
            "merge_pull_request",
            "Merge a pull request with a merge, squash or rebase commit.",
            [
                ("repo", "string", "Repository as owner/name.", True),
                ("pull_number", "integer", "Pull request number.", True),
                ("merge_method", "string", "merge, squash or rebase.", False),
            ],
            "squash and merge pull request 17",
        ),
        _tool(
            "list_commits",
            "List commits on a branch of a repository, newest first.",
            [
                ("repo", "string", "Repository as owner/name.", True),
                ("sha", "string", "Branch or commit to list from.", False),
                ("author", "string", "Only commits by this author.", False),
            ],
            "list the latest commits on the main branch of the repository",
        ),
    ),
    "chat": (
        _tool(
            "post_message",
            "Post a message to a channel, optionally in a thread.",
            [
                ("channel", "string", "Channel ID or name.", True),
                ("text", "string", "Message text.", True),
                ("thread_ts", "string", "Timestamp of the parent message.", False),
            ],
            "post a message in the team channel saying the deploy is done",
        ),
        _tool(
            "list_channels",
            "List the channels the workspace has, with member counts.",
            [
                ("limit", "integer", "Most channels to return.", False),
                ("include_archived", "boolean", "Include archived channels.", False),
            ],
            "list all the channels in our workspace",
        ),
        _tool(
            "get_channel_history",
            "Read recent messages from a channel, newest first.",
            [
                ("channel", "string", "Channel ID or name.", True),
                ("limit", "integer", "Most messages to return.", False),
            ],
            "read the recent messages in the support channel history",
        ),
        _tool(
            "add_reaction",
            "Add an emoji reaction to a message.",
            [
                ("channel", "string", "Channel ID or name.", True),
                ("timestamp", "string", "Timestamp of the message.", True),
                ("emoji", "string", "Emoji name without colons.", True),
            ],
            "add a thumbs up emoji reaction to that message",
        ),
        _tool(
            "search_messages",
            "Search messages across channels the user can read.",
            [
                ("query", "string", "Words to search for.", True),
                ("count", "integer", "Most matches to return.", False),
            ],
            "search messages across channels for the outage postmortem",
        ),
        _tool(
            "get_user_profile",
            "Get a workspace member's profile: name, title, time zone and status.",
            [("user_id", "string", "Member ID.", True)],
            "what time zone is in her member profile",
        ),
    ),
    "files": (
        _tool(
            "read_text_file",
            "Read a text file from an allowed directory, optionally only its head or tail.",
            [
                ("path", "string", "Absolute path of the file.", True),
                ("head", "integer", "Only the first N lines.", False),
                ("tail", "integer", "Only the last N lines.", False),
            ],
            "read the last lines of the text file server.log",
        ),
        _tool(
            "write_file",
            "Create a file or overwrite it with new content.",
            [
                ("path", "string", "Absolute path of the file.", True),
                ("content", "string", "Text to write.", True),
            ],
            "write a file notes.txt with the content buy milk",
        ),
        _tool(
            "list_directory",
            "List the files and subdirectories in a directory.",
            [("path", "string", "Absolute path of the directory.", True)],
            "list the directory contents of my projects folder",
        ),
        _tool(
            "move_file",
            "Move or rename a file or directory.",
            [
                ("source", "string", "Current path.", True),
                ("destination", "string", "New path.", True),
            ],
            "move the file draft.md to the archive folder",
        ),
        _tool(
            "search_files",
            "Find files whose names match a pattern under a directory, recursively.",
            [
                ("path", "string", "Directory to search under.", True),
                ("pattern", "string", "Glob pattern to match names against.", True),
                ("exclude", "array", "Patterns to skip.", False),
            ],
            "find files matching *.csv under the data directory",
        ),
        _tool(
            "get_file_info",
            "Get a file's size, creation and modification times and permissions.",
            [("path", "string", "Absolute path of the file.", True)],
            "when was the file report.pdf last modified and what size is it",
        ),
    ),
    "calendar": (
        _tool(
            "list_events",
            "List calendar events between two times.",
            [
                (
                    "calendar_id",
                    "string",
                    "Calendar to read; primary by default.",
                    False,
                ),
                ("time_min", "string", "Start of the range, RFC 3339.", True),
                ("time_max", "string", "End of the range, RFC 3339.", True),
            ],
            "what calendar events do I have between monday and friday",
        ),
        _tool(
            "create_event",
            "Create a calendar event with attendees and an optional video link.",
            [
                ("summary", "string", "Event title.", True),
                ("start", "string", "Start time, RFC 3339.", True),
                ("end", "string", "End time, RFC 3339.", True),
                ("attendees", "array", "Attendee email addresses.", False),
                ("add_video_link", "boolean", "Attach a video call link.", False),
            ],
            "create a calendar event called design review tomorrow at 3pm with attendees",
        ),
        _tool(
            "find_free_time",
            "Find time slots when every listed attendee is free.",
            [
                ("attendees", "array", "Email addresses to check.", True),
                ("duration_minutes", "integer", "Length of the slot needed.", True),
                ("time_min", "string", "Earliest start, RFC 3339.", True),
                ("time_max", "string", "Latest end, RFC 3339.", True),
            ],
            "find free time when all three attendees can meet for 30 minutes",
        ),
        _tool(
            "delete_event",
            "Delete a calendar event and notify its attendees.",
            [
                ("event_id", "string", "Event to delete.", True),
                ("notify", "boolean", "Email the attendees.", False),
            ],
            "delete the calendar event for friday standup",
        ),
        _tool(
            "update_event",
            "Change an event's time, title, description or attendees.",
            [
                ("event_id", "string", "Event to change.", True),
                ("start", "string", "New start time, RFC 3339.", False),
                ("end", "string", "New end time, RFC 3339.", False),
                ("summary", "string", "New title.", False),
            ],
            "move the event to start at 4pm instead and update its title",
        ),
    ),
    "tracker": (
        _tool(
            "create_ticket",
            "Create a ticket in a project with a type and priority.",
            [
                ("project", "string", "Project key.", True),
                ("summary", "string", "One-line summary.", True),
                ("description", "string", "Full description.", False),
                ("ticket_type", "string", "Bug, Task or Story.", False),
                ("priority", "string", "Highest to Lowest.", False),
            ],
            "create a ticket in the project for the broken export button with high priority",
        ),
        _tool(
            "search_tickets",
            "Search tickets with a query in the tracker's query language.",
            [
                ("query", "string", "Query to run.", True),
                ("max_results", "integer", "Most tickets to return.", False),
            ],
            "search tickets assigned to me that are still unresolved",
        ),
        _tool(
            "transition_ticket",
            "Move a ticket to another workflow status, such as In Progress or Done.",
            [
                ("ticket_key", "string", "Ticket key, e.g. PROJ-12.", True),
                ("status", "string", "Status to move to.", True),
            ],
            "move ticket PROJ-12 to the done workflow status",
        ),
        _tool(
            "get_sprint",
            "Get the active sprint of a board: goal, dates and its tickets.",
            [("board_id", "integer", "Board to read.", True)],
            "what is the goal of the active sprint on our board",
        ),
        _tool(
            "log_work",
            "Log time spent on a ticket.",
            [
                ("ticket_key", "string", "Ticket key, e.g. PROJ-12.", True),
                ("time_spent", "string", "Time such as 2h 30m.", True),
                ("comment", "string", "What the time went on.", False),
            ],
            "log work of 2 hours time spent on PROJ-7",
        ),
    ),
}

#: Default report grid: connected servers x tools per server.
DEFAULT_SERVER_COUNTS: Tuple[int, ...] = (1, 3, 5)
DEFAULT_TOOLS_PER_SERVER: Tuple[int, ...] = (5,)
#: Every kind lists at least this many tools.
MAX_TOOLS_PER_SERVER = min(len(tools) for tools in SERVER_KINDS.values())


class MockMCPClient:
    """The slice of ``MCPClient`` that tool conversion reads."""

    def __init__(self, name: str, tools: Sequence[MockToolSpec], kind: str = ""):
        self.name = name
        self.prefix = name
        self.kind = kind or name
        self.specs = tuple(tools)

    def list_tools(self) -> List[MCPTool]:
        return [spec.to_mcp_tool() for spec in self.specs]


def build_mock_servers(n_servers: int, tools_per_server: int) -> List[MockMCPClient]:
    """*n_servers* mock servers with *tools_per_server* tools each.

    Servers cycle through :data:`SERVER_KINDS`; a kind used twice gets a numbered
    name (``repos_2``), the way two configured servers of one kind would.

    Raises:
        ValueError: A count is below 1, or *tools_per_server* is more than the
            smallest kind lists.
    """
    if n_servers < 1 or tools_per_server < 1:
        raise ValueError("n_servers and tools_per_server must be at least 1")
    if tools_per_server > MAX_TOOLS_PER_SERVER:
        raise ValueError(
            f"tools_per_server={tools_per_server} is more than the "
            f"{MAX_TOOLS_PER_SERVER} tools "
            "every mock server kind has; add tools to SERVER_KINDS in "
            "gaia/eval/mcp_fixture.py to measure more."
        )
    kinds = list(SERVER_KINDS)
    servers = []
    for i in range(n_servers):
        kind = kinds[i % len(kinds)]
        round_ = i // len(kinds)
        name = kind if round_ == 0 else f"{kind}_{round_ + 1}"
        servers.append(
            MockMCPClient(name, SERVER_KINDS[kind][:tools_per_server], kind=kind)
        )
    return servers


def convert_mock_servers(
    agent: Any, servers: Sequence[MockMCPClient]
) -> Dict[str, Dict[str, Any]]:
    """Registry entries for *servers*, from GAIA's own MCP conversion.

    Raises:
        ValueError: Two servers produce the same tool name.
    """
    from gaia.mcp.mixin import MCPClientMixin

    entries: Dict[str, Dict[str, Any]] = {}
    for server in servers:
        # A mock carries the name, prefix and list_tools() conversion reads.
        client = cast("MCPClient", server)
        for _, entry in MCPClientMixin._convert_mcp_tools(agent, client):
            if entry["name"] in entries:
                raise ValueError(f"duplicate mock MCP tool name {entry['name']!r}")
            entries[entry["name"]] = entry
    return entries


def recall_probes(servers: Sequence[MockMCPClient]) -> List[Tuple[str, str]]:
    """``(request, registry tool name)`` for every tool of *servers*.

    A second server of a kind lists the same tools, so only the first of each
    kind is probed: the same request twice would count one result twice.
    """
    probes = []
    seen = set()
    for server in servers:
        if server.kind in seen:
            continue
        seen.add(server.kind)
        probes += [
            (spec.probe, f"mcp_{server.prefix}_{spec.name}") for spec in server.specs
        ]
    return probes


_WORD = re.compile(r"[a-z0-9]+")
_STOPWORDS = frozenset(
    "a an and are as at be by can do for from i in is it me my of on or our "
    "that the this to was what when which with".split()
)


def lexical_embedder(dim: int = 512) -> Callable[[str], Any]:
    """A deterministic, model-free stand-in for the loader's embedder.

    Hashes each word into one of *dim* buckets and L2-normalizes, so the
    loader's dot products are word-overlap cosines. It is no judge of meaning:
    recall measured with it shows that MCP tools take part in selection and
    surface when a request matches them, which is what a CI guard can check
    without a model. A real embedder is the way to measure recall quality.
    """
    import numpy as np

    def embed(text: str) -> "np.ndarray":
        vec = np.zeros(dim, dtype=np.float32)
        for word in _WORD.findall(text.lower().replace("_", " ")):
            if word in _STOPWORDS:
                continue
            bucket = int.from_bytes(hashlib.sha256(word.encode()).digest()[:4], "big")
            vec[bucket % dim] += 1.0
        norm = float(np.linalg.norm(vec))
        return vec / norm if norm else vec

    return embed
