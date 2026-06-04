# Copyright(C) 2024-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""GAIA Summarizer agent — standalone hub package.

Installs the ``summarize`` agent into the GAIA registry via the ``gaia.agent``
entry-point group (see ``pyproject.toml``). The framework's
``AgentRegistry._discover_installed_agents`` calls :func:`build_registration`
at discovery time; the agent module itself is imported lazily inside the
factory so discovery stays cheap.
"""

# ``SummarizerAgent`` is re-exported lazily via ``__getattr__`` (below) so that
# importing this package at registry-discovery time does not pull in the heavy
# agent module; it is therefore intentionally absent from ``__all__``.
__all__ = ["build_registration"]

__version__ = "0.1.0"


def __getattr__(name):
    # Lazy re-export so ``import gaia_agent_summarize`` (e.g. at registry
    # discovery) does not pull in the heavy agent module + its SDK deps.
    if name == "SummarizerAgent":
        from gaia_agent_summarize.agent import SummarizerAgent

        return SummarizerAgent
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def build_registration():
    """Return the :class:`AgentRegistration` for the summarize agent."""
    from gaia.agents.registry import AgentRegistration, class_factory

    def factory(**kwargs):
        from gaia_agent_summarize.agent import SummarizerAgent

        return class_factory(SummarizerAgent)(**kwargs)

    return AgentRegistration(
        id="summarize",
        name="Summarizer",
        description="Document and text summarization — PDFs, transcripts, and email",
        source="installed",
        conversation_starters=[
            "Summarize this document",
            "Give me the key action items from this transcript",
        ],
        factory=factory,
        agent_dir=None,
        models=["Qwen3-4B-Instruct-2507-GGUF"],
        namespaced_agent_id="installed:summarize",
        category="productivity",
        tags=["summarize", "documents", "transcripts"],
        icon="file-text",
        tools_count=0,
    )
