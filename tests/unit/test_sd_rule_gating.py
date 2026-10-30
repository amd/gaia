"""The prompt only teaches image generation when the session can do it."""

import pytest

pytest.importorskip("gaia_agent_chat")

from tests.unit.test_profilespec_characterization import (  # noqa: E402
    chat_agent_build_context,
)


@pytest.mark.parametrize("enabled", [False, True])
def test_image_generation_rule_follows_the_setting(enabled):
    with chat_agent_build_context("full", enable_sd_tools=enabled) as agent:
        prompt = agent._get_system_prompt()
    assert ("CALL `generate_image` first" in prompt) is enabled
    assert ("Not available in this session" in prompt) is not enabled


@pytest.mark.parametrize("enabled", [False, True])
def test_slides_are_files_that_ask_for_a_format(enabled):
    with chat_agent_build_context("full", enable_sd_tools=enabled) as agent:
        prompt = agent._get_system_prompt()
    assert "**SLIDES / DECKS:** Asked to make one (not just mentioned)" in prompt
    assert "a file, never an image" in prompt
    assert 'always ask "PowerPoint (.pptx) or HTML?"' in prompt
    assert "build nothing until they answer" in prompt
    assert "`run_python` with python-pptx" in prompt
    if enabled:
        assert "never for slides or documents" in prompt
