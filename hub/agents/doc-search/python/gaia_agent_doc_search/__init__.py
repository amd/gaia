# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""GAIA Doc Search agent — standalone hub package (reference example).

A RAG agent built by *composing a framework tool mixin*: it inherits
``RAGToolsMixin`` to get document indexing and retrieval tools for free. Use it
as a copy-paste starting point for an agent that answers questions over the
user's documents.

Installing this package registers the ``doc-search`` agent in the GAIA registry
via the ``gaia.agent`` entry-point group (see ``pyproject.toml``). The
framework's ``AgentRegistry._discover_installed_agents`` calls
:func:`build_registration` at discovery time; the agent module itself is
imported lazily inside the factory so discovery stays cheap.
"""

# ``DocSearchAgent`` is re-exported lazily via ``__getattr__`` (below) so
# importing this package at registry-discovery time does not pull in the agent
# module; it is therefore intentionally absent from ``__all__``.
__all__ = ["build_registration"]

__version__ = "0.1.0"


def __getattr__(name):
    # Lazy re-export so ``import gaia_agent_doc_search`` (e.g. at registry
    # discovery) does not pull in the agent module + its SDK deps.
    if name == "DocSearchAgent":
        from gaia_agent_doc_search.agent import DocSearchAgent

        return DocSearchAgent
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def build_registration():
    """Return the :class:`AgentRegistration` for the doc-search agent."""
    from gaia.agents.registry import AgentRegistration, class_factory

    def factory(**kwargs):
        from gaia_agent_doc_search.agent import DocSearchAgent

        return class_factory(DocSearchAgent)(**kwargs)

    return AgentRegistration(
        id="doc-search",
        name="Doc Search",
        description=(
            "RAG reference agent — composes the framework RAGToolsMixin for "
            "document Q&A"
        ),
        source="installed",
        conversation_starters=[
            "Index the documents in ./docs and tell me what they cover",
            "What does my report say about Q3 revenue?",
        ],
        factory=factory,
        agent_dir=None,
        models=["Gemma-4-E4B-it-GGUF"],
        namespaced_agent_id="installed:doc-search",
        category="examples",
        tags=["example", "reference", "rag", "documents", "starter"],
        icon="file-search",
        tools_count=10,
    )
