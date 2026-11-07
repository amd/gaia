# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""The GAIA plugin for Claude Code: its manifests agree with each other and
with the ``gaia-agent`` flag they launch."""

import json
import re
from pathlib import Path

from gaia_agent.entry import MCP_SELECTOR

REPO = Path(__file__).resolve().parents[2]
MARKETPLACE = REPO / ".claude-plugin" / "marketplace.json"


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _plugin_entry() -> dict:
    (entry,) = [p for p in _load(MARKETPLACE)["plugins"] if p["name"] == "gaia"]
    return entry


def _plugin_dir() -> Path:
    return (REPO / _plugin_entry()["source"]).resolve()


def test_the_marketplace_points_at_a_plugin_with_the_same_name_and_version():
    manifest = _load(_plugin_dir() / ".claude-plugin" / "plugin.json")
    assert manifest["name"] == _plugin_entry()["name"]
    assert manifest["version"] == _plugin_entry()["version"]


def test_the_mcp_server_launches_the_agent_binary_in_mcp_mode():
    servers = _load(_plugin_dir() / ".mcp.json")["mcpServers"]
    assert servers["gaia"] == {
        "type": "stdio",
        "command": "gaia-agent",
        "args": [MCP_SELECTOR],
    }


def test_every_skill_names_itself_after_its_folder():
    skills = sorted((_plugin_dir() / "skills").glob("*/SKILL.md"))
    assert skills
    for skill in skills:
        name = re.search(r"^name:\s*(\S+)\s*$", skill.read_text(encoding="utf-8"), re.M)
        assert name and name.group(1) == skill.parent.name
