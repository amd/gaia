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


# The discovery block as "file" and "full" render it; the flagship runs "full",
# so these bytes must not move when the doc profile's wording changes.
_FS_DISCOVERY_RULES = """
**SMART DISCOVERY WORKFLOW:**
1. Domain question + nothing relevant indexed: `find_files` with 1-2 document-type keywords (handbook / report / manual / policy / guide), NOT the question's content terms. If nothing after 2 tries, `browse_directory`.
2. Files found → `index_document` immediately (no confirmation), then query and answer in the same turn.
3. Already indexed → query directly.

**NEVER ASK PERMISSION TO INDEX.** "Would you like me to index this?" is BANNED. If a document is referenced and you can locate it, index + query + answer in one flow.

**SEARCH LOOP PREVENTION:** Same `find_files` / `browse_directory` query twice with same result → STOP and acknowledge.

**VAGUE FOLLOW-UP ("what about X?"):** find_files("X") → index_document → query_specific_file with whatever question is implicit, or "key topics overview" if none.
"""


@pytest.mark.parametrize("profile", ["file", "full", "no-such-profile"])
def test_filesystem_profiles_discovery_rules_are_unchanged(profile):
    """Profiles that register find_files keep the original discovery bytes."""
    prompt = _prompt_for_profile(profile)
    assert _FS_DISCOVERY_RULES in prompt


def test_doc_profile_discovery_rules_use_search_file(prompts_and_tools):
    prompt, registered = prompts_and_tools["doc"]
    assert {"search_file", "browse_directory"} <= registered
    assert "find_files" not in prompt
    assert _FS_DISCOVERY_RULES.replace("find_files", "search_file") in prompt


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


def _prompt_for_profile(profile):
    with (
        patch.dict("os.environ", {"GAIA_MEMORY_DISABLED": "1"}),
        patch("gaia_agent_chat.agent.RAGSDK"),
        patch("gaia_agent_chat.agent.RAGConfig"),
        patch("gaia.llm.lemonade_manager.LemonadeManager.ensure_ready"),
        patch("gaia.llm.lemonade_client.LemonadeClient"),
    ):
        agent = ChatAgent(ChatAgentConfig(silent_mode=True, prompt_profile=profile))
        return agent._get_system_prompt()


def _prompt_for_spec(spec):
    """Render the prompt with ``spec`` substituted for the data profile."""
    import gaia_agent_chat.agent as agent_mod

    with (
        patch.dict("os.environ", {"GAIA_MEMORY_DISABLED": "1"}),
        patch("gaia_agent_chat.agent.RAGSDK"),
        patch("gaia_agent_chat.agent.RAGConfig"),
        patch("gaia.llm.lemonade_manager.LemonadeManager.ensure_ready"),
        patch("gaia.llm.lemonade_client.LemonadeClient"),
    ):
        agent = ChatAgent(ChatAgentConfig(silent_mode=True, prompt_profile="data"))
        with patch.object(agent_mod, "get_profile_spec", return_value=spec):
            return _unsupported_line(agent._get_system_prompt())


@pytest.mark.parametrize(
    "groups, web, media",
    [
        (("media_transcribe",), False, True),
        (("web_browse",), True, False),
        (("web_browse", "media_transcribe"), True, True),
        # Near-miss group names must not unlock the sentences.
        (("web_browse_extra", "media"), False, False),
    ],
)
def test_web_and_media_sentences_follow_tool_groups_independently(groups, web, media):
    import dataclasses

    spec = dataclasses.replace(
        PROFILE_SPECS["data"], tool_groups=PROFILE_SPECS["data"].tool_groups + groups
    )
    line = _prompt_for_spec(spec)
    assert ("search_web" in line) is web
    assert ("transcribe_media" in line) is media
    assert "Image analysis IS supported via `analyze_image`." in line


def test_unknown_profile_falls_back_to_full_and_keeps_web_and_media():
    """An unrecognised profile registers full's tools, so it must advertise them."""
    with (
        patch.dict("os.environ", {"GAIA_MEMORY_DISABLED": "1"}),
        patch("gaia_agent_chat.agent.RAGSDK"),
        patch("gaia_agent_chat.agent.RAGConfig"),
        patch("gaia.llm.lemonade_manager.LemonadeManager.ensure_ready"),
        patch("gaia.llm.lemonade_client.LemonadeClient"),
    ):
        agent = ChatAgent(
            ChatAgentConfig(silent_mode=True, prompt_profile="no-such-profile")
        )
        prompt = agent._get_system_prompt()
        registered = set(agent._tools_registry)
    assert {"search_web", "transcribe_media"} <= registered
    line = _unsupported_line(prompt)
    assert "search_web" in line and "transcribe_media" in line
