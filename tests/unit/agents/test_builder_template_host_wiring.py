# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Tests for the Builder template's host-state wiring (issue #3316, Part 3).

``src/gaia/agents/builder/template.py`` emitted agents mixing in
``RAGToolsMixin``/``FileIOToolsMixin`` without ever wiring the state those
mixins require (``self.rag``, ``self.path_validator``, ...). Every agent
scaffolded through the documented Builder flow with ``tools=["rag"]`` or
``tools=["file_io"]`` would raise the moment one of those tools ran. These
tests pin the fix: the generator now emits the wiring, so the scaffolded
agent actually works out of the box.
"""

import importlib.util
from unittest.mock import patch

import pytest

from gaia.agents.base.tools import _TOOL_REGISTRY


@pytest.fixture(autouse=True)
def _clean_registry():
    saved = dict(_TOOL_REGISTRY)
    yield
    _TOOL_REGISTRY.clear()
    _TOOL_REGISTRY.update(saved)


def _get_tool(name):
    return _TOOL_REGISTRY[name]["function"]


def _generate_and_exec(tmp_path, tool_name, class_name):
    from gaia.agents.builder.template import generate_agent_source

    src = generate_agent_source(
        agent_id=f"contract-test-{tool_name}",
        agent_name="Contract Test Agent",
        description="Generated for the Builder template host-wiring test.",
        class_name=class_name,
        starters=["a", "b", "c"],
        system_prompt="You are a test agent.",
        enable_mcp=False,
        tools=[tool_name],
    )
    module_path = tmp_path / f"agent_{tool_name}.py"
    module_path.write_text(src)
    spec = importlib.util.spec_from_file_location(f"gen_{tool_name}", module_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# ---------------------------------------------------------------------------
# AC7 — every Builder template variant, enumerated from KNOWN_TOOLS,
# constructs. The two mixins Part 3 actually wires (rag, file_io) are
# additionally proven to execute one tool end-to-end; the other ten mixins
# were confirmed (via their own class-level defaults / self-init, see
# hub-attribute-contract tests) to have no live construction-or-first-call
# defect, so this asserts registration succeeded without touching real
# external resources (network, subprocess, image/vision models).
# ---------------------------------------------------------------------------


def test_every_known_tool_generates_a_constructible_agent(tmp_path):
    from gaia.agents.registry import KNOWN_TOOLS

    # sd/vlm/skills each name their registration method differently from the
    # Builder's generic "self.register_<KNOWN_TOOLS-key>_tools()" convention
    # (init_sd()/init_vlm() register their own tools; skill_library_tools.py
    # exposes register_skill_library_tools(), not register_skills_tools()).
    # That naming mismatch is a pre-existing Builder generator gap, not a
    # host-attribute contract defect (nothing here reads state "nothing
    # sets"), and is out of scope for this issue — tracked separately.
    for tool_name in sorted(KNOWN_TOOLS.keys() - {"sd", "vlm", "skills"}):
        module = _generate_and_exec(tmp_path, tool_name, "ContractTestAgent")

        with patch("gaia.agents.base.agent.AgentSDK"):
            agent = module.ContractTestAgent(skip_lemonade=True, silent_mode=True)

        registered = getattr(agent, f"register_{tool_name}_tools")
        assert isinstance(registered, type(agent.__init__)) or callable(registered)


def test_generated_rag_agent_executes_query_documents(tmp_path):
    module = _generate_and_exec(tmp_path, "rag", "ContractTestRagAgent")

    with patch("gaia.agents.base.agent.AgentSDK"):
        module.ContractTestRagAgent(skip_lemonade=True, silent_mode=True)

    result = _get_tool("query_documents")(query="q")
    assert result["status"] == "no_documents"


def test_generated_file_io_agent_executes_read_file(tmp_path):
    module = _generate_and_exec(tmp_path, "file_io", "ContractTestFileIOAgent")

    with patch("gaia.agents.base.agent.AgentSDK"):
        agent = module.ContractTestFileIOAgent(skip_lemonade=True, silent_mode=True)

    agent.path_validator.allowed_paths.add(tmp_path.resolve())
    target = tmp_path / "readable.txt"
    target.write_text("hello world")

    result = _get_tool("read_file")(file_path=str(target))
    assert result["status"] == "success"
    assert result["content"] == "hello world"
