# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""GAIA Word Count agent — standalone hub package (reference example).

A single-tool agent: it shows how to register a tool with the ``@tool``
decorator and let the LLM call it. Use it as a copy-paste starting point for a
tool-using agent.

Installing this package registers the ``word-count`` agent in the GAIA
registry via the ``gaia.agent`` entry-point group (see ``pyproject.toml``).
The framework's ``AgentRegistry._discover_installed_agents`` calls
:func:`build_registration` at discovery time; the agent module itself is
imported lazily inside the factory so discovery stays cheap.
"""

# ``WordCountAgent`` is re-exported lazily via ``__getattr__`` (below) so
# importing this package at registry-discovery time does not pull in the agent
# module; it is therefore intentionally absent from ``__all__``.
__all__ = ["build_registration"]

__version__ = "0.1.0"


def __getattr__(name):
    # Lazy re-export so ``import gaia_agent_word_count`` (e.g. at registry
    # discovery) does not pull in the agent module + its SDK deps.
    if name == "WordCountAgent":
        from gaia_agent_word_count.agent import WordCountAgent

        return WordCountAgent
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def build_registration():
    """Return the :class:`AgentRegistration` for the word-count agent."""
    from gaia.agents.registry import AgentRegistration, class_factory

    def factory(**kwargs):
        from gaia_agent_word_count.agent import WordCountAgent

        return class_factory(WordCountAgent)(**kwargs)

    return AgentRegistration(
        id="word-count",
        name="Word Count",
        description="Single-tool reference agent — demonstrates the @tool decorator",
        source="installed",
        conversation_starters=[
            "Count the words in: the quick brown fox",
            "How many sentences are in this paragraph?",
        ],
        factory=factory,
        agent_dir=None,
        models=["Gemma-4-E4B-it-GGUF"],
        namespaced_agent_id="installed:word-count",
        category="examples",
        tags=["example", "reference", "tools", "starter"],
        icon="calculator",
        tools_count=1,
    )
