# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Every ``array`` parameter in a tool schema carries ``items``.

gpt-oss's chat template reads ``param_spec['items']`` on each array
parameter. Without the key, llama.cpp's template engine resolves it to the
dict's ``items`` method and raises "Function is not a bool value", so Lemonade
answered every flagship request with HTTP 500 before the model ran at all.

No LLM or external service required.
"""

from typing import List, Optional, Tuple
from unittest.mock import patch

import pytest

from gaia.agents.base.tools import _TOOL_REGISTRY, tool


@pytest.fixture
def registry():
    saved = dict(_TOOL_REGISTRY)
    yield _TOOL_REGISTRY
    _TOOL_REGISTRY.clear()
    _TOOL_REGISTRY.update(saved)


def test_the_element_type_is_recorded(registry):
    @tool
    def pick(
        names: List[str],
        ids: Optional[List[int]] = None,
        raw: list = None,
        sizes: Tuple[int, ...] = (),
        pair: Tuple[int, str] = (0, ""),
    ) -> dict:
        """Pick some.

        Args:
            names: Names.
            ids: Ids.
            raw: Anything.
            sizes: Sizes.
            pair: A pair.
        """
        return {}

    params = registry["pick"]["parameters"]
    assert params["names"]["items"] == {"type": "string"}
    assert params["ids"]["items"] == {"type": "integer"}
    assert params["raw"]["items"] == {}
    assert params["sizes"]["items"] == {"type": "integer"}
    assert params["pair"]["items"] == {}


def test_an_mcp_array_keeps_its_items():
    from gaia.mcp.client.mcp_client import MCPTool

    entry = MCPTool(
        name="search",
        description="Search.",
        input_schema={
            "properties": {
                "tags": {"type": "array", "items": {"type": "string"}},
                "any": {"type": "array"},
            },
            "required": ["tags"],
        },
    ).to_gaia_format("srv")
    assert entry["parameters"]["tags"]["items"] == {"type": "string"}
    assert "items" not in entry["parameters"]["any"]


def test_the_flagship_sends_items_on_every_array(monkeypatch):
    pytest.importorskip("gaia_agent")
    monkeypatch.setenv("GAIA_MEMORY_DISABLED", "1")
    from gaia_agent.agent import GaiaAgent, GaiaAgentConfig

    with patch("gaia.agents.base.agent.AgentSDK"):
        agent = GaiaAgent(GaiaAgentConfig(silent_mode=True, streaming=False))
    missing = []
    arrays = 0
    for schema in agent._build_openai_tool_schemas():
        fn = schema["function"]
        for name, prop in fn["parameters"]["properties"].items():
            if prop["type"] == "array":
                arrays += 1
                # An untyped ``items: {}`` invites small models to send objects.
                if not prop.get("items", {}).get("type"):
                    missing.append(f"{fn['name']}.{name}")
    assert arrays, "no array parameter left to check"
    assert not missing, f"array parameters without a typed items: {missing}"
