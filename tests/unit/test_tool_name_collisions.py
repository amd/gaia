# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Two different tools may not claim one name in an agent's tool set (#3506, #4776)."""

import pytest

from gaia.agents.base.tools import (
    _TOOL_REGISTRY,
    DuplicateToolError,
    tool,
    tool_build,
)


@pytest.fixture(autouse=True)
def _clean_registry():
    saved = dict(_TOOL_REGISTRY)
    _TOOL_REGISTRY.clear()
    yield
    _TOOL_REGISTRY.clear()
    _TOOL_REGISTRY.update(saved)


class _SearchMixin:
    def register_search(self):
        @tool
        def read_file(path: str) -> dict:
            """Read a file, capped."""
            return {"from": "search", "path": path}


class _IOMixin:
    def register_io(self, **kwargs):
        @tool(**kwargs)
        def read_file(path: str) -> dict:
            """Read a file."""
            return {"from": "io", "path": path}


def test_a_second_tool_with_a_taken_name_raises_naming_both():
    host = type("Host", (_SearchMixin, _IOMixin), {})()
    with tool_build():
        host.register_search()
        with pytest.raises(DuplicateToolError) as caught:
            host.register_io()

    message = str(caught.value)
    assert "'read_file'" in message
    assert "_SearchMixin.register_search.<locals>.read_file" in message
    assert "_IOMixin.register_io.<locals>.read_file" in message
    assert "test_tool_name_collisions.py:" in message
    assert "override=True" in message
    # The first tool keeps the name; nothing was replaced.
    assert _TOOL_REGISTRY["read_file"]["function"]("x")["from"] == "search"


def test_re_registering_the_same_tool_is_allowed():
    # Each registrar call makes a new closure, as an agent rebuild does.
    host = _SearchMixin()
    with tool_build():
        host.register_search()
        first = _TOOL_REGISTRY["read_file"]["function"]
        host.register_search()

    assert _TOOL_REGISTRY["read_file"]["function"] is not first


def test_override_replaces_explicitly():
    host = type("Host", (_SearchMixin, _IOMixin), {})()
    with tool_build():
        host.register_search()
        host.register_io(override=True)

    assert _TOOL_REGISTRY["read_file"]["function"]("x")["from"] == "io"


def test_override_must_be_a_bool():
    with pytest.raises(TypeError, match="override"):

        @tool(override="yes")
        def some_tool() -> dict:
            """A tool."""
            return {}


def test_a_new_agent_build_replaces_an_earlier_builds_tool():
    # The global registry is shared across agents in one process; a tool from
    # an earlier agent's build is not a collision in the next one.
    with tool_build():
        _SearchMixin().register_search()
    with tool_build():
        _IOMixin().register_io()

    assert _TOOL_REGISTRY["read_file"]["function"]("x")["from"] == "io"


def test_an_instance_registry_rejects_a_collision():
    destination = {}

    @tool(registry=destination)
    def lookup() -> dict:
        """First."""
        return {}

    with pytest.raises(DuplicateToolError, match="'lookup'"):

        @tool(registry=destination)
        def lookup() -> dict:  # noqa: F811
            """Second."""
            return {}


def test_an_agent_whose_tools_collide_fails_to_build():
    from gaia.agents.base.agent import Agent

    class _CollidingAgent(Agent, _SearchMixin, _IOMixin):
        def _get_system_prompt(self) -> str:
            return "test"

        def _register_tools(self) -> None:
            self.register_search()
            self.register_io()

    with pytest.raises(DuplicateToolError, match="'read_file'"):
        _CollidingAgent(skip_lemonade=True, silent_mode=True)
