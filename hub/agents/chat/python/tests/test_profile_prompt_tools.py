# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""A profile's prompt must not name a tool that profile does not register."""

import re
from unittest.mock import patch

import pytest
from gaia_agent_chat.agent import ChatAgent, ChatAgentConfig
from gaia_agent_chat.profiles import PROFILE_SPECS

# Prompt names with no registered tool; remove an entry once its prompt is fixed.
_KNOWN_GAPS = {
    "chat": {"query_specific_file"},  # appears only inside a banned-output example
    "doc": {"find_files"},
}


@pytest.fixture(scope="module")
def prompts_and_tools():
    """Build one agent per profile; return {profile: (prompt, registered tools)}."""
    built = {}
    with (
        patch.dict("os.environ", {"GAIA_MEMORY_DISABLED": "1"}),
        patch("gaia_agent_chat.agent.RAGSDK"),
        patch("gaia_agent_chat.agent.RAGConfig"),
        patch("gaia.llm.lemonade_manager.LemonadeManager.ensure_ready"),
        patch("gaia.llm.lemonade_client.LemonadeClient"),
    ):
        for profile in PROFILE_SPECS:
            agent = ChatAgent(ChatAgentConfig(silent_mode=True, prompt_profile=profile))
            built[profile] = (agent._get_system_prompt(), set(agent._tools_registry))
    return built


@pytest.mark.parametrize("profile", list(PROFILE_SPECS))
def test_prompt_names_only_registered_tools(profile, prompts_and_tools):
    known = set().union(*(tools for _, tools in prompts_and_tools.values()))
    prompt, registered = prompts_and_tools[profile]
    named = {name for name in known if re.search(rf"\b{re.escape(name)}\b", prompt)}
    assert named - registered == _KNOWN_GAPS.get(profile, set())


def test_data_profile_does_not_advertise_web_or_media(prompts_and_tools):
    prompt, _ = prompts_and_tools["data"]
    assert "Web browsing IS supported" not in prompt
    assert "transcribe_media" not in prompt


def test_full_profile_advertises_web_and_media(prompts_and_tools):
    prompt, _ = prompts_and_tools["full"]
    assert "search_web" in prompt
    assert "transcribe_media" in prompt


def _unsupported_line(prompt):
    lines = [line for line in prompt.splitlines() if "**UNSUPPORTED:**" in line]
    assert len(lines) == 1, lines
    return lines[0]


def test_full_profile_unsupported_line_is_unchanged(prompts_and_tools):
    """The flagship runs "full"; its prompt bytes must not move with this gate."""
    line = _unsupported_line(prompts_and_tools["full"][0])
    assert line.endswith(
        "feature_request.md . Web browsing IS supported via `search_web` / "
        "`fetch_page` / `download_file`. Image analysis IS supported via "
        "`analyze_image`. Audio and video recordings ARE supported via "
        "`transcribe_media` — never refuse an .mp4/.m4a/.mp3/.wav as something "
        "you cannot process."
    )


def test_data_profile_unsupported_line_keeps_image_analysis(prompts_and_tools):
    line = _unsupported_line(prompts_and_tools["data"][0])
    assert line.endswith(
        "feature_request.md . Image analysis IS supported via `analyze_image`."
    )
    assert "search_web" not in line and "transcribe_media" not in line
