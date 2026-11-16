import pytest

# ChatAgent ships in the flagship gaia-agent-gaia wheel (#6099).
pytest.importorskip("gaia_agent")

from gaia_agent.chat_agent import ChatAgent  # noqa: E402


def test_system_prompt_size_under_limit():
    """Ensure system prompt stays under a safe budget to avoid regressions like #1030.

    This is a small guard: if the system prompt exceeds 30k chars, it may trigger
    huge context sizes and long runtimes. Tune threshold as needed.
    """
    agent = ChatAgent()
    prompt = agent._get_system_prompt()
    assert isinstance(prompt, str)
    assert len(prompt) < 30000, f"System prompt too large: {len(prompt)} chars"
