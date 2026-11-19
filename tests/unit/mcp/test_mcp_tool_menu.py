# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""The menu of MCP tools a filtered tool list leaves out (#1972).

It goes into every system prompt of a session with unlisted MCP tools, and its
names come from third-party servers, so its size has a hard ceiling whatever
the servers declare.
"""

from __future__ import annotations

import logging

import pytest

from gaia.mcp.mixin import (
    MCP_MENU_HEADER,
    MCP_MENU_MAX_TOOLS,
    MCP_MENU_NAME_CHARS,
    format_unlisted_mcp_tools,
)


def _mcp(server: str, tool: str, description: str = "Does a thing.") -> dict:
    return {
        "description": f"[MCP:{server}] {description}",
        "_mcp_server": server,
        "_mcp_tool": tool,
    }


def _registry(*tools: tuple[str, str]) -> dict:
    reg = {"read_file": {"description": "Read a file."}}
    reg.update({f"mcp_{s}_{t}": _mcp(s, t) for s, t in tools})
    return reg


def _servers(menu: str) -> list[str]:
    return menu.splitlines()[2:]


def test_nothing_unlisted_renders_nothing():
    registry = _registry(("files", "move"))
    assert format_unlisted_mcp_tools(registry, list(registry)) == ""
    assert format_unlisted_mcp_tools({"read_file": {}}, []) == ""


def test_one_line_per_server_with_bare_tool_names():
    registry = _registry(
        ("files", "move"), ("files", "copy"), ("calendar", "create_event")
    )
    menu = format_unlisted_mcp_tools(registry, ["read_file"])
    lines = menu.splitlines()
    assert lines[0] == MCP_MENU_HEADER
    assert "search_tools" in lines[1] and "load_tools" not in menu
    assert _servers(menu) == ["- files: move, copy", "- calendar: create_event"]
    assert "read_file" not in menu and "Does a thing" not in menu


def test_a_listed_tool_stays_off_and_its_server_goes_when_empty():
    registry = _registry(("files", "move"), ("calendar", "create_event"))
    menu = format_unlisted_mcp_tools(registry, ["mcp_files_move"])
    assert _servers(menu) == ["- calendar: create_event"]


def test_the_server_label_is_the_prefix_the_full_name_uses():
    """A server named "My Files" registers ``mcp_my_files_*``; the label must match."""
    entry = {"description": "", "_mcp_server": "My Files", "_mcp_tool": "move"}
    menu = format_unlisted_mcp_tools({"mcp_my_files_move": entry}, [])
    assert _servers(menu) == ["- my_files: move"]


def test_an_entry_without_a_tool_name_falls_back_to_the_full_name():
    entry = {"description": "", "_mcp_server": "x"}
    assert _servers(format_unlisted_mcp_tools({"mcp_x_ping": entry}, [])) == [
        "- x: mcp_x_ping"
    ]


def test_the_menu_renders_the_same_bytes_each_time():
    registry = _registry(("b", "t1"), ("a", "t2"), ("b", "t3"))
    first = format_unlisted_mcp_tools(registry, [])
    assert _servers(first) == ["- b: t1, t3", "- a: t2"]
    assert format_unlisted_mcp_tools(dict(registry), []) == first


def test_a_withheld_tool_stays_off_the_menu():
    registry = _registry(("files", "move"), ("files", "delete"))
    menu = format_unlisted_mcp_tools(
        registry, [], permitted=lambda name: name != "mcp_files_delete"
    )
    assert _servers(menu) == ["- files: move"]


def test_past_the_cap_servers_are_counted_and_the_cut_is_logged(caplog):
    tools = [("big", f"t{i:02d}") for i in range(MCP_MENU_MAX_TOOLS - 2)]
    tools += [("mid", f"m{i}") for i in range(5)] + [("late", "x"), ("late", "y")]
    with caplog.at_level(logging.INFO):
        menu = format_unlisted_mcp_tools(_registry(*tools), [])
    lines = _servers(menu)
    assert lines[1] == "- mid: m0, m1, and 3 more"
    assert lines[2] == "- late: 2 tools"
    names = sum(len(line.split(": ", 1)[1].split(", ")) for line in lines[:1])
    assert names == MCP_MENU_MAX_TOOLS - 2
    total = MCP_MENU_MAX_TOOLS + 5
    assert any(
        f"names {MCP_MENU_MAX_TOOLS} of {total}" in r.getMessage()
        for r in caplog.records
    )


def test_under_the_cap_nothing_is_logged(caplog):
    with caplog.at_level(logging.INFO):
        format_unlisted_mcp_tools(_registry(("files", "move")), [])
    assert not [r for r in caplog.records if "MCP tool menu" in r.getMessage()]


def test_an_overlong_tool_name_is_counted_not_shown():
    long_tool = "x" * (MCP_MENU_NAME_CHARS + 1)
    menu = format_unlisted_mcp_tools(_registry(("s", long_tool), ("s", "ok")), [])
    assert _servers(menu) == ["- s: ok, and 1 more"]


@pytest.mark.parametrize("count", [MCP_MENU_MAX_TOOLS, 1000])
def test_a_hostile_server_cannot_grow_the_menu_past_its_ceiling(count):
    tool = "n" * MCP_MENU_NAME_CHARS
    registry = {}
    for i in range(count):
        entry = _mcp("s", f"{i:04d}{tool}"[:MCP_MENU_NAME_CHARS], "z" * 10_000)
        registry[f"mcp_s_{entry['_mcp_tool']}"] = entry
    menu = format_unlisted_mcp_tools(registry, [])
    head = len("\n".join(menu.splitlines()[:2])) + 1
    names = MCP_MENU_MAX_TOOLS * (MCP_MENU_NAME_CHARS + len(", "))
    tail = len("- s: , and 9999 more")
    assert len(menu) <= head + names + tail
    assert "z" * 20 not in menu
