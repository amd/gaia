# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""GAIA Document Q&A agent — standalone hub package.

Registers the ``docqa`` agent (RAG document Q&A) into the GAIA registry via the
``gaia.agent`` entry-point group. It is a building-block agent, hidden from the
UI selector by default. The agent module is imported lazily so registry
discovery stays cheap.
"""

# Re-exported lazily via ``__getattr__``; intentionally absent from ``__all__``.
__all__ = ["build_registration"]

__version__ = "0.1.0"

_LAZY = {"DocumentQAAgent": "agent", "DocumentQAAgentConfig": "agent"}


def __getattr__(name):
    if name in _LAZY:
        import importlib

        module = importlib.import_module(f"gaia_agent_docqa.{_LAZY[name]}")
        return getattr(module, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def build_registration():
    """Return the :class:`AgentRegistration` for the docqa agent."""
    from gaia.agents.registry import AgentRegistration, class_factory

    def factory(**kwargs):
        from gaia_agent_docqa.agent import DocumentQAAgent

        return class_factory(DocumentQAAgent)(**kwargs)

    return AgentRegistration(
        id="docqa",
        name="Document Q&A",
        description="RAG-focused agent for document Q&A and indexing",
        source="installed",
        conversation_starters=[
            "Index the documents in ./docs and summarize them",
            "What does my report say about Q3 revenue?",
        ],
        factory=factory,
        agent_dir=None,
        models=["Qwen3.5-35B-A3B-GGUF"],
        hidden=True,
        namespaced_agent_id="installed:docqa",
        category="productivity",
        tags=["rag", "documents", "qa", "retrieval"],
        icon="file-search",
        tools_count=0,
    )
