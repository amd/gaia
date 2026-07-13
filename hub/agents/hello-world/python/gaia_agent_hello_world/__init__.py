# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""GAIA Hello World agent — standalone hub package (reference example).

The smallest possible GAIA agent: a system prompt and nothing else. Use it as
a copy-paste starting point for a new conversational agent.

Installing this package registers the ``hello-world`` agent in the GAIA
registry via the ``gaia.agent`` entry-point group (see ``pyproject.toml``).
The framework's ``AgentRegistry._discover_installed_agents`` calls
:func:`build_registration` at discovery time; the agent module itself is
imported lazily inside the factory so discovery stays cheap.
"""

# ``HelloWorldAgent`` is re-exported lazily via ``__getattr__`` (below) so
# importing this package at registry-discovery time does not pull in the heavy
# agent module; it is therefore intentionally absent from ``__all__``.
__all__ = ["build_registration"]

__version__ = "0.1.0"


def __getattr__(name):
    # Lazy re-export so ``import gaia_agent_hello_world`` (e.g. at registry
    # discovery) does not pull in the agent module + its SDK deps.
    if name == "HelloWorldAgent":
        from gaia_agent_hello_world.agent import HelloWorldAgent

        return HelloWorldAgent
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def build_registration():
    """Return the :class:`AgentRegistration` for the hello-world agent."""
    from gaia.agents.registry import AgentRegistration, class_factory

    def factory(**kwargs):
        from gaia_agent_hello_world.agent import HelloWorldAgent

        return class_factory(HelloWorldAgent)(**kwargs)

    return AgentRegistration(
        id="hello-world",
        name="Hello World",
        description=(
            "Minimal conversational reference agent — the smallest possible "
            "GAIA agent"
        ),
        source="installed",
        conversation_starters=[
            "Say hello",
            "What can a GAIA agent do?",
        ],
        factory=factory,
        agent_dir=None,
        models=["Gemma-4-E4B-it-GGUF"],
        namespaced_agent_id="installed:hello-world",
        category="examples",
        tags=["example", "reference", "conversation", "starter"],
        icon="sparkles",
        tools_count=0,
    )
