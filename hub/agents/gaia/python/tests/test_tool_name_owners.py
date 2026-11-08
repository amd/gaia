# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Every flagship profile builds with one owner per tool name (#3506, #4776).

A tool that takes a name another mixin already registered must say so with
``@tool(override=True)``, or the build raises. This pins which implementation
owns each shared name today, so a reordered registrar can't flip it silently.
"""

from __future__ import annotations

import contextlib

import pytest
from gaia_agent.agent import GaiaAgent, GaiaAgentConfig
from gaia_agent.profiles import PROFILE_SPECS

from gaia.agents.base.tools import _TOOL_REGISTRY

FILE_IO = "gaia.agents.tools.file_io_tools.FileIOToolsMixin"
FILE_SEARCH = "gaia.agents.tools.file_tools.FileSearchToolsMixin"

BROWSER = "gaia.agents.tools.browser_tools.BrowserToolsMixin"
PERPLEXITY = "gaia_agent.chat_agent.ChatAgent._register_external_tools_conditional"

# name -> {profile: owner}; a profile without the tool is left out.
OWNERS = {
    "read_file": {
        "doc": FILE_SEARCH,
        "file": FILE_IO,
        "data": FILE_IO,
        "full": FILE_IO,
    },
    "write_file": {
        "doc": FILE_SEARCH,
        "file": FILE_IO,
        "data": FILE_IO,
        "full": FILE_IO,
    },
    "edit_file": {
        "doc": FILE_SEARCH,
        "file": FILE_IO,
        "data": FILE_IO,
        "full": FILE_IO,
    },
    "browse_directory": {
        "doc": FILE_SEARCH,
        "file": FILE_SEARCH,
        "data": FILE_SEARCH,
        "full": FILE_SEARCH,
    },
}


@contextlib.contextmanager
def _agent(profile, perplexity):
    saved = dict(_TOOL_REGISTRY)
    _TOOL_REGISTRY.clear()
    try:
        with pytest.MonkeyPatch.context() as mp:
            mp.setenv("GAIA_MEMORY_DISABLED", "1")
            if perplexity:
                mp.setenv("PERPLEXITY_API_KEY", "test-key")
            else:
                mp.delenv("PERPLEXITY_API_KEY", raising=False)
            yield GaiaAgent(
                config=GaiaAgentConfig(
                    silent_mode=True, prompt_profile=profile, skip_lemonade=True
                )
            )
    finally:
        _TOOL_REGISTRY.clear()
        _TOOL_REGISTRY.update(saved)


def _source(agent, name):
    function = agent._tools_registry[name]["function"]
    return f"{function.__module__}.{function.__qualname__}"


@pytest.mark.parametrize("perplexity", [False, True], ids=["no-key", "perplexity"])
@pytest.mark.parametrize("profile", sorted(PROFILE_SPECS))
def test_every_profile_builds_with_one_owner_per_name(profile, perplexity):
    with _agent(profile, perplexity) as agent:
        for name, owners in OWNERS.items():
            if profile in owners:
                assert _source(agent, name).startswith(owners[profile] + "."), name
        if "search_web" in agent._tools_registry:
            owner = PERPLEXITY if perplexity else BROWSER
            assert _source(agent, "search_web").startswith(owner + ".")
