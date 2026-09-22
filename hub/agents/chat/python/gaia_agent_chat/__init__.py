# Copyright(C) 2024-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""GAIA Chat agent — the flagship's base class, no longer a product of its own.

``ChatAgent`` is what ``GaiaAgent`` subclasses, so this package remains a hard
dependency of ``gaia-agent`` and is fully supported as a library. What it is no
longer is a *selectable agent*: the flagship supersedes all three profiles, and
all three registrations carry ``hidden=True`` so they are absent from the Agent
UI picker and the Hub catalog.

They stay registered — ``registry.get("doc")`` still resolves — because stored
sessions, the ``*-lite`` id aliases, and the eval scenarios address them by id.
Hidden removes the *choice*, not the *route*:

* ``chat`` — general conversation (lean prompt, no document tools)
* ``doc``  — document Q&A with RAG
* ``file`` — file-system navigation/search

Public names are re-exported lazily so registry discovery stays cheap. The
registry's ``_discover_installed_agents`` stamps ``source="installed"``, the
``installed:<id>`` namespaced id, and the namespaced-id factory wrapper.
"""

__all__ = ["build_chat", "build_doc", "build_file"]

__version__ = "0.1.0"

_LAZY = {
    "ChatAgent": "agent",
    "ChatAgentConfig": "agent",
    "ChatAgentLite": "lite_agent",
}


def __getattr__(name):
    if name in _LAZY:
        import importlib

        module = importlib.import_module(f"gaia_agent_chat.{_LAZY[name]}")
        return getattr(module, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def _make_factory(profile, extra=None, tiers=None):
    """ChatAgent factory honouring a ``model_tier`` kwarg (#1162)."""
    import dataclasses

    from gaia.agents.registry import _select_tier_model

    _extra = dict(extra or {})
    _tiers = list(tiers or [])

    def factory(**kwargs):
        tier = kwargs.pop("model_tier", None)
        if tier:
            preset = _select_tier_model(_tiers, tier)
            if preset:
                kwargs.setdefault("model_id", preset)

        from gaia_agent_chat.agent import ChatAgent, ChatAgentConfig

        valid_fields = {f.name for f in dataclasses.fields(ChatAgentConfig)}
        filtered = {k: v for k, v in kwargs.items() if k in valid_fields}
        filtered.setdefault("prompt_profile", profile)
        for k, v in _extra.items():
            filtered.setdefault(k, v)
        return ChatAgent(config=ChatAgentConfig(**filtered))

    return factory


def build_chat():
    """Return the :class:`AgentRegistration` for the ``chat`` profile."""
    from gaia.agents.registry import AgentRegistration, build_model_tiers

    tiers = build_model_tiers("Full")
    return AgentRegistration(
        id="chat",
        name="Chat",
        description="General conversation — fast, personality-first, no document tools",
        source="installed",
        # Retired as a choice; still resolvable by id for stored sessions.
        hidden=True,
        conversation_starters=[
            "What can you help me with?",
            "Tell me about yourself",
            "What's new today?",
        ],
        factory=_make_factory("chat", tiers=tiers),
        agent_dir=None,
        models=[],
        required_connections=[],
        # Mirrors ChatAgent.CONSUMES_MCP_SERVERS — the lazy factory must not
        # import the chat module at discovery time. A guard test keeps these
        # in sync.
        consumes_mcp_servers=True,
        category="conversation",
        tags=["chat", "general", "personality"],
        icon="message-circle",
        # Introspected registry size for prompt_profile="chat" (shell tools
        # only) — drift-guarded by tests/unit/test_chat_fix_contracts.py.
        tools_count=1,
        model_tiers=tiers,
    )


def build_doc():
    """Return the :class:`AgentRegistration` for the ``doc`` profile."""
    from gaia.agents.registry import AgentRegistration, build_model_tiers

    tiers = build_model_tiers("Full")
    return AgentRegistration(
        id="doc",
        name="Doc Agent",
        description="Document Q&A with RAG — ask questions about PDFs, reports, and manuals",
        source="installed",
        # Retired as a choice; still resolvable by id for stored sessions and evals.
        hidden=True,
        conversation_starters=[
            "Search my documents for...",
            "Summarize this document",
            "What does the report say about...",
        ],
        factory=_make_factory("doc", tiers=tiers),
        agent_dir=None,
        models=[],
        required_connections=[],
        category="documents",
        tags=["rag", "files", "search", "mcp"],
        icon="file-text",
        # Introspected registry size for prompt_profile="doc" — drift-guarded
        # by tests/unit/test_chat_fix_contracts.py.
        tools_count=38,
        model_tiers=tiers,
    )


def build_file():
    """Return the :class:`AgentRegistration` for the ``file`` profile."""
    from gaia.agents.registry import AgentRegistration, build_model_tiers

    tiers = build_model_tiers("Full")
    return AgentRegistration(
        id="file",
        name="File Agent",
        description="File system navigation, search, and analysis",
        source="installed",
        # Retired as a choice; still resolvable by id for stored sessions.
        hidden=True,
        conversation_starters=[
            "Find files related to...",
            "What's in my Documents folder?",
            "Show me the project structure",
        ],
        factory=_make_factory("file", extra={"enable_filesystem": True}, tiers=tiers),
        agent_dir=None,
        models=[],
        required_connections=[],
        category="productivity",
        tags=["files", "search", "filesystem", "shell"],
        icon="folder-search",
        # Introspected registry size for prompt_profile="file" (with
        # enable_filesystem=True, matching this factory's extra kwarg) —
        # drift-guarded by tests/unit/test_chat_fix_contracts.py.
        tools_count=35,
        model_tiers=tiers,
    )
