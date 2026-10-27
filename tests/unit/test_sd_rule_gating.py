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
    assert ("Always CALL `generate_image`" in prompt) is enabled
    assert ("Not available in this session" in prompt) is not enabled


def test_a_slide_request_is_not_steered_to_image_generation():
    """A one-slide framework came back as a Stable Diffusion picture."""
    with chat_agent_build_context("full", enable_sd_tools=True) as agent:
        prompt = agent._get_system_prompt()
    rule = prompt[prompt.index("**IMAGE GENERATION:**") :].split("\n", 1)[0]
    assert "first for a picture" in rule
    assert "slide, deck, chart or document is a file, not a picture" in rule
