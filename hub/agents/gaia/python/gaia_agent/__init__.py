# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""GAIA — the flagship general-purpose agent, as a standalone hub package.

Registers one agent id, ``gaia``, via the ``gaia.agent`` entry-point group.
Public names are re-exported lazily so registry discovery stays cheap: importing
this module must not drag in RAG, the browser stack, or a model client.
"""

__all__ = ["build_gaia"]

__version__ = "0.1.0"

_LAZY = {
    "GaiaAgent": "agent",
    "GaiaAgentConfig": "agent",
}


def __getattr__(name):
    if name in _LAZY:
        import importlib

        module = importlib.import_module(f"gaia_agent.{_LAZY[name]}")
        return getattr(module, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def _factory(**kwargs):
    """Build a :class:`GaiaAgent`, honouring the registry's ``model_tier`` kwarg."""
    import dataclasses

    from gaia.agents.registry import _select_tier_model, build_model_tiers

    tier = kwargs.pop("model_tier", None)
    if tier:
        preset = _select_tier_model(build_model_tiers("Full"), tier)
        if preset:
            kwargs.setdefault("model_id", preset)

    from gaia_agent.agent import GaiaAgent, GaiaAgentConfig

    valid = {f.name for f in dataclasses.fields(GaiaAgentConfig)}
    return GaiaAgent(
        config=GaiaAgentConfig(**{k: v for k, v in kwargs.items() if k in valid})
    )


def build_gaia():
    """Return the :class:`AgentRegistration` for the flagship ``gaia`` agent."""
    from gaia.agents.registry import AgentRegistration, build_model_tiers

    tiers = build_model_tiers("Full")
    return AgentRegistration(
        id="gaia",
        name="GAIA",
        description=(
            "The flagship agent — conversation, document Q&A, data analysis, and "
            "web research, with memory that persists and skills you can add"
        ),
        source="installed",
        conversation_starters=[
            "What can you do?",
            "Summarize the documents in this folder",
            "Research this topic and write me a brief",
            "What did we talk about last time?",
        ],
        factory=_factory,
        agent_dir=None,
        models=[],
        required_connections=[],
        category="general",
        tags=["general", "chat", "rag", "memory", "skills"],
        icon="sparkles",
        # Must equal the real registry size for the default construction, and
        # the manifest's own tools_count. Drift-guarded by tests/test_gaia_agent.py.
        tools_count=66,
        # ChatAgent loads MCP servers dynamically, so the Settings "Active for"
        # panel must list this agent for MCP-server connectors.
        consumes_mcp_servers=True,
        model_tiers=tiers,
    )
