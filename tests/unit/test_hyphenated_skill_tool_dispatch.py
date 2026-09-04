# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Skill-namespaced tools register with a literal hyphen (``rss-digest/fetch_rss``).

_execute_tool used to blanket-replace ``-`` -> ``_`` before the registry lookup on
the theory that tool names are always snake_case — which made every hyphenated
skill's tools undispatchable ("Unknown tool name"). This pins the fix: the exact
registered name resolves, while the hyphen->underscore normalization still rescues
a genuine snake_case model typo.
"""

from gaia.agents.base.agent import Agent


class _DispatchStub(Agent):
    def __init__(self, registry):
        self._instance_tools = registry

    def _register_tools(self):
        pass

    def _resolve_tool_name(self, name):
        return None


def _tool(result):
    def fn(**kwargs):
        return {"status": "success", "result": result}

    return {"function": fn, "description": "", "parameters": {}}


def test_hyphenated_skill_tool_dispatches_to_exact_name():
    agent = _DispatchStub({"rss-digest/fetch_rss": _tool("FEED")})
    out = agent._execute_tool("rss-digest/fetch_rss", {"url": "http://x"})
    assert out["status"] == "success"
    assert out["result"] == "FEED"


def test_hyphen_normalization_still_rescues_a_snake_case_typo():
    # No exact match; the model emitted a hyphen for an underscore tool.
    agent = _DispatchStub({"read_file": _tool("OK")})
    out = agent._execute_tool("read-file", {})
    assert out["status"] == "success"
    assert out["result"] == "OK"


def test_unknown_tool_still_errors():
    agent = _DispatchStub({"read_file": _tool("OK")})
    out = agent._execute_tool("nope/does_not_exist", {})
    assert out["status"] == "error"
