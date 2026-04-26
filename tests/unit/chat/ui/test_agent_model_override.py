# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Regression test for issue #841: custom agent's model_id ignored by UI."""

import textwrap
from unittest.mock import patch

from gaia.agents.registry import AgentRegistry


def test_issue_841_custom_python_agent_model_id_respected(tmp_path):
    """A custom Python agent using kwargs.setdefault in __init__ must be
    instantiated with its own model_id when the UI omits the kwarg.

    On pre-fix main, the UI always passes model_id=<session model> explicitly
    to registry.create_agent — defeating kwargs.setdefault, which only fires
    when the key is ABSENT. After T3 lands, the UI omits model_id when no
    explicit user choice exists, so setdefault fires as the agent intends.

    This test simulates the fixed UI call pattern: calling create_agent without
    model_id, and asserting the agent's declared default is respected.
    """
    agents_dir = tmp_path / ".gaia" / "agents" / "foo"
    agents_dir.mkdir(parents=True)
    (agents_dir / "agent.py").write_text(textwrap.dedent("""
        from gaia.agents.base.agent import Agent

        class FooAgent(Agent):
            AGENT_ID = "foo"
            AGENT_NAME = "Foo"

            def __init__(self, **kwargs):
                kwargs.setdefault("model_id", "Qwen3.5-4B-GGUF")
                super().__init__(skip_lemonade=True, **kwargs)

            def _get_system_prompt(self):
                return "foo"

            def _register_tools(self):
                pass
    """))

    with patch("gaia.agents.registry.Path.home", return_value=tmp_path):
        registry = AgentRegistry()
        registry.discover()

    reg = registry.get("foo")
    assert reg is not None, "custom agent should be discovered under patched HOME"

    # Simulate what the fixed UI does when no explicit user choice exists:
    # OMIT the model_id kwarg entirely so setdefault fires.
    agent = registry.create_agent("foo", silent_mode=True, debug=False)

    assert agent.model_id == "Qwen3.5-4B-GGUF", (
        f"Issue #841: custom agent's kwargs.setdefault('model_id', ...) must "
        f"govern when UI omits the kwarg; got {agent.model_id!r}"
    )
