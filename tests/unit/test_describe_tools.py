# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""``Agent.describe_tools`` and ``tool_category`` (#440).

What a UI shows as the agent's tools: name, the description the model gets,
and where the tool comes from.
"""

import threading
from types import SimpleNamespace

import pytest

from gaia.agents.base.agent import Agent
from gaia.agents.base.tools import describe_registry, tool, tool_category
from gaia.agents.registry import KNOWN_TOOLS
from gaia.mcp.client.mcp_client import MCPTool


def _agent_with(registry):
    return SimpleNamespace(_tools_registry=registry)


@pytest.mark.parametrize(
    "name,module_path", [(k, mod) for k, (mod, _cls) in KNOWN_TOOLS.items()]
)
def test_every_known_mixin_module_is_its_own_category(name, module_path):
    entry = {"function": SimpleNamespace(__module__=module_path)}
    assert tool_category("some_tool", entry) == name


def test_a_tool_the_agent_defines_itself_is_agent():
    local = {}

    @tool(registry=local)
    def greet(name: str) -> str:
        """Say hello."""
        return name

    assert tool_category("greet", local["greet"]) == "agent"


def test_a_skill_tool_is_skill_even_when_defined_in_a_mixin_module():
    entry = {"function": SimpleNamespace(__module__="gaia.agents.tools.file_io_tools")}
    assert tool_category("rss-digest/write_file", entry) == "skill"


def test_mcp_tools_are_mcp_and_two_servers_with_one_tool_name_stay_apart():
    tool_def = MCPTool(name="search", description="Search.", input_schema={})
    registry = {}
    for server in ("github", "jira"):
        entry = tool_def.to_gaia_format(server)
        registry[entry["name"]] = entry

    described = describe_registry(registry)

    assert [(t["name"], t["category"]) for t in described] == [
        ("mcp_github_search", "mcp"),
        ("mcp_jira_search", "mcp"),
    ]


def test_mcp_wins_over_a_slash_in_the_name():
    assert tool_category("mcp_a/b", {"_mcp_server": "a"}) == "mcp"


def test_a_tool_with_no_function_is_agent_not_an_error():
    assert tool_category("x", {}) == "agent"


def test_describe_tools_reads_the_agents_own_registry_in_order():
    registry = {
        "b_tool": {"description": "B.", "function": None},
        "a_tool": {"description": None, "function": None},
    }

    described = Agent.describe_tools(_agent_with(registry))

    assert described == [
        {"name": "b_tool", "description": "B.", "category": "agent"},
        {"name": "a_tool", "description": "", "category": "agent"},
    ]


def test_describe_tools_returns_only_the_three_public_fields():
    registry = {
        "t": {
            "description": "T.",
            "function": lambda: None,
            "parameters": {"x": {}},
            "preflight": object(),
        }
    }

    (only,) = Agent.describe_tools(_agent_with(registry))

    assert set(only) == {"name", "description", "category"}


def test_describe_tools_survives_a_skill_loading_tools_meanwhile():
    """A running turn can add tools while the panel reads them."""
    registry = {f"t{i}": {"description": "", "function": None} for i in range(200)}
    stop = threading.Event()

    def churn():
        i = 0
        while not stop.is_set():
            registry[f"skill/x{i}"] = {"description": "", "function": None}
            registry.pop(f"skill/x{i - 50}", None)
            i += 1

    worker = threading.Thread(target=churn, daemon=True)
    worker.start()
    try:
        for _ in range(200):
            Agent.describe_tools(_agent_with(registry))
    finally:
        stop.set()
        worker.join(timeout=5)
