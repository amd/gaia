"""The prompt only teaches image generation when the session can do it."""

import pytest

from tests.unit.test_profilespec_characterization import (
    chat_agent_build_context,
)


@pytest.mark.parametrize("enabled", [False, True])
def test_image_generation_rule_follows_the_setting(enabled):
    with chat_agent_build_context("full", enable_sd_tools=enabled) as agent:
        prompt = agent._get_system_prompt()
    assert ("Always CALL `generate_image`" in prompt) is enabled
    assert ("Not available in this session" in prompt) is not enabled
