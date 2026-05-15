from gaia.agents.chat.agent import ChatAgent


def test_system_prompt_size_under_limit():
    """Ensure system prompt stays under a safe budget to avoid regressions like #1030.

    This is a small guard: if the system prompt exceeds 30k chars, it may trigger
    huge context sizes and long runtimes. Tune threshold as needed.
    """
    agent = ChatAgent()
    prompt = agent._get_system_prompt()
    assert isinstance(prompt, str)
    assert len(prompt) < 30000, f"System prompt too large: {len(prompt)} chars"
