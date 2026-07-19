# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""DocSearchAgent — a RAG agent built by composing a framework tool mixin.

This is a *reference example* that shows the most powerful pattern in GAIA:
**you rarely write retrieval code yourself — you compose a mixin.** By
inheriting :class:`gaia.agents.tools.RAGToolsMixin`, this agent gets a full set
of document tools (index, query, list, summarize, …) for free.

The pattern
-----------
1. Inherit ``Agent`` and the mixin(s) you want:
   ``class DocSearchAgent(Agent, RAGToolsMixin):``
2. Give the mixin the dependency it expects. ``RAGToolsMixin`` reads
   ``self.rag``, so construct a :class:`gaia.rag.sdk.RAGSDK` in ``__init__``
   *before* calling ``super().__init__()``.
3. Activate the mixin's tools in ``_register_tools()`` by calling its
   registration hook (``self.register_rag_tools()``).
4. Write a system prompt that tells the model how to use those tools.

Other framework mixins follow the same shape — see ``KNOWN_TOOLS`` in
``gaia.agents.registry`` for the full list (file_io, shell, browser, …).
"""

from typing import Optional

from gaia.agents.base import Agent
from gaia.agents.tools import RAGToolsMixin

_SYSTEM_PROMPT = """\
You are GAIA's Doc Search agent. You answer questions using the user's own
documents, retrieved with RAG (retrieval-augmented generation).

How to work:
- If the user points you at files or a directory, index them first
  (index_document / index_directory).
- To answer a question, call query_documents to retrieve the most relevant
  chunks, then answer ONLY from what you retrieved.
- Always cite the source file(s) you used. If retrieval returns nothing
  relevant, say so — never invent an answer.
- Use list_indexed_documents and rag_status when the user asks what you know.
"""


class DocSearchAgent(Agent, RAGToolsMixin):
    """A document-Q&A agent composed from the framework ``RAGToolsMixin``."""

    AGENT_ID = "doc-search"
    AGENT_NAME = "Doc Search"
    AGENT_DESCRIPTION = (
        "RAG reference agent — composes the framework RAGToolsMixin for " "document Q&A"
    )
    CONVERSATION_STARTERS = [
        "Index the documents in ./docs and tell me what they cover",
        "What does my report say about Q3 revenue?",
    ]

    # RAG benefits from a stronger model than the trivial examples.
    DEFAULT_MODEL = "Gemma-4-E4B-it-GGUF"

    def __init__(self, model_id: Optional[str] = None, **kwargs):
        from gaia.rag.sdk import RAGSDK, RAGConfig

        model = model_id or self.DEFAULT_MODEL
        # RAGToolsMixin reads ``self.rag`` — wire it up before super().__init__()
        # so the tools have their dependency when _register_tools() runs.
        self.rag = RAGSDK(RAGConfig(model=model))

        self.response_mode = "conversational"
        super().__init__(model_id=model, **kwargs)

    def _get_system_prompt(self) -> str:
        return _SYSTEM_PROMPT

    def _register_tools(self) -> None:
        # The mixin registers all RAG tools; we just turn them on.
        self.register_rag_tools()
        # Freeze this agent's tools so they don't leak into other agents
        # running in the same process.
        self._snapshot_tools()
